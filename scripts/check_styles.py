"""Offline checks for style cards (code kind + refine + drag-related APIs)."""

from __future__ import annotations

from pathlib import Path

from src.agent.styles import (
    delete_style_card,
    format_active_styles_preamble,
    get_style_card,
    list_style_cards,
    refine_style_card,
    save_style_card,
)
from src.agent.permissions import PermissionGate, ApprovalMode
from src.tools import build_default_registry


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "demos"
    for sid in ("unit-test-voice", "py-unit-style"):
        try:
            delete_style_card(root, sid)
        except Exception:
            pass

    card = save_style_card(
        root,
        style_id="py-unit-style",
        name="紧凑 Python",
        description="短函数、类型注解",
        body="- 命名：snake_case\n- 偏好小函数\n- 样例：def add(a: int, b: int) -> int:",
        kind="code",
    )
    assert card.kind == "code"
    refined = refine_style_card(
        root,
        style_id="py-unit-style",
        additions="- 错误处理：少用裸 except\n- 测试：*_test.py 与模块同目录",
        note="from greeter_test.py",
    )
    assert "Learned update" in refined.body or "少用裸 except" in refined.body
    assert refined.kind == "code"

    pre = format_active_styles_preamble(root, ["py-unit-style"])
    assert "Active style cards" in pre
    assert "code" in pre

    gate = PermissionGate(root, approval=ApprovalMode.AUTO)

    def ask(q: str) -> str:
        return "是"

    reg = build_default_registry(gate, ask_user_fn=ask)
    assert reg.get("refine_style") is not None
    out = reg.dispatch(
        "refine_style",
        {
            "id": "py-unit-style",
            "additions": "- 导入：标准库在前",
            "confirm": True,
        },
    )
    assert "Refined" in out or "refine" in out.lower() or "py-unit-style" in out

    delete_style_card(root, "py-unit-style")
    assert get_style_card(root, "py-unit-style") is None
    assert not any(c.id == "py-unit-style" for c in list_style_cards(root))
    print("style cards (code+refine) ok")


if __name__ == "__main__":
    main()
