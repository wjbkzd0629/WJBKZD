#!/usr/bin/env python3
"""Validate the structure and delivery integrity of an A-share pledge-risk report."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path


SECTION_GROUPS = {
    "结论": ("核心结论", "结论先行", "结论卡"),
    "公司概况": ("公司概况",),
    "行业": ("行业概况", "行业分析"),
    "同业": ("竞争对手", "同业比较", "可比公司"),
    "经营业绩": ("经营业绩", "业绩分析"),
    "财务质量": ("财务质量",),
    "合规监管": ("合规", "监管风险"),
    "造假预警": ("造假预警", "预警清单"),
    "股价技术": ("股价", "技术分析"),
    "估值": ("估值",),
    "舆情": ("舆情",),
    "风险排序": ("主要风险", "风险排序"),
    "尽调问题": ("尽调问题", "尽调清单"),
    "质押专项": ("质押风险专项", "股票质押风险", "质押专项"),
    "来源": ("来源索引", "数据来源", "参考资料"),
}

RISK_LAYERS = {
    "上市公司主体": ("上市公司主体", "主体信用", "主体经营"),
    "出质人": ("出质人", "回购能力"),
    "抵押品": ("抵押品", "质物风险", "股价波动"),
    "法律处置": ("法律处置", "处置风险", "处置可执行"),
}

LOCAL_PATH_PATTERNS = (
    r"file://",
    r"(?:^|[\s(])/(?:Users|home)/[^\s)]+",
    r"(?:^|[\s(])[A-Za-z]:\\[^\s)]+",
    r"(?:^|[\s(])~[/\\][^\s)]+",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def validate(report: Path, copy: Path | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not report.is_file():
        return [f"报告不存在：{report}"], warnings

    text = report.read_text(encoding="utf-8")
    top = "\n".join(text.splitlines()[:80])

    if not text.startswith("---"):
        warnings.append("建议使用 YAML frontmatter，便于 Obsidian 管理")
    software = re.search(r"撰写软件\s*[：:]\s*(\S.+)", top)
    model = re.search(r"AI\s*模型\s*[：:]\s*(\S.+)", top, re.I)
    if "撰写声明" not in top:
        errors.append("报告顶部缺少“撰写声明”")
    if software is None:
        errors.append("报告顶部缺少“撰写软件：实际软件名称”")
    if model is None:
        errors.append("报告顶部缺少“AI 模型：实际模型名称/版本”")
    elif re.fullmatch(r"(?:AI|人工智能|大模型)[。；;]?", model.group(1).strip(), re.I):
        errors.append("AI 模型字段过于笼统，必须写实际模型名称/版本或明确无法核验")
    if not re.search(r"20\d{2}[-年./]\d{1,2}[-月./]\d{1,2}", top):
        errors.append("报告顶部缺少可识别的研究截至日期")
    if "A股" not in top and "A 股" not in top:
        errors.append("报告顶部未明确仅分析 A 股")

    for label, terms in SECTION_GROUPS.items():
        if not has_any(text, terms):
            errors.append(f"缺少必需章节：{label}")

    for label, terms in RISK_LAYERS.items():
        if not has_any(text, terms):
            errors.append(f"未单列风险层级：{label}")

    if text.count("http://") + text.count("https://") < 8:
        errors.append("可点击来源链接少于 8 个，证据链可能不足")
    source_mentions = len(re.findall(r"\[S\d+\]|\[\^.+?\]|来源[：:]", text))
    if source_mentions < 10:
        warnings.append("来源标记少于 10 处，请检查关键数字和结论是否就近引用")

    if "已触发" not in text and "部分触发" not in text and "触发关注" not in text:
        errors.append("造假预警表缺少触发类状态（已触发/部分触发）")
    for status in ("未触发", "待核验"):
        if status not in text:
            errors.append(f"造假预警表缺少状态：{status}")

    if "暂未查证到可靠资料" not in text:
        warnings.append("未出现“暂未查证到可靠资料”，请确认所有关键未知项确已验证")
    if any(re.search(pattern, text, re.MULTILINE) for pattern in LOCAL_PATH_PATTERNS):
        errors.append("正文包含本地绝对路径、主目录路径或 file:// 链接，不应作为金融事实来源或公开内容")
    if "平仓线" in text and not re.search(r"不.{0,12}(估算|给出|推测).{0,30}平仓线|平仓线.{0,30}(待核验|未披露|未知|不.{0,8}给出)", text):
        warnings.append("报告提到平仓线，但未清晰说明披露/待核验边界")

    if copy is not None:
        if not copy.is_file():
            errors.append(f"交付副本不存在：{copy}")
        elif sha256(report) != sha256(copy):
            errors.append("工作区报告与交付副本 SHA-256 不一致")
        if not any(re.fullmatch(r"(?:00-)?inbox", part, re.I) for part in copy.parts):
            errors.append("Obsidian 交付副本不在 Inbox/00-Inbox 目录内")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Markdown report path")
    parser.add_argument("--copy", type=Path, help="Optional Obsidian copy path")
    args = parser.parse_args()

    errors, warnings = validate(args.report, args.copy)
    for item in warnings:
        print(f"WARN: {item}")
    for item in errors:
        print(f"ERROR: {item}")

    if errors:
        print(f"FAIL: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1

    report_hash = sha256(args.report)
    lines = len(args.report.read_text(encoding="utf-8").splitlines())
    print(f"PASS: {args.report} | lines={lines} | sha256={report_hash}")
    if args.copy:
        print(f"COPY MATCH: {args.copy}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
