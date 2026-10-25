#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clitodo —— 一个零依赖的命令行待办事项工具。

数据默认存在 ~/.clitodo/todos.json（可用环境变量 CLITODO_DB 指定其他位置）。

    python todo.py add "写周报" --priority high --due 2026-10-31
    python todo.py ls
    python todo.py done 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

VERSION = "1.0.0"

# 优先级：数字越大越急
PRIORITIES = {"high": 3, "normal": 2, "low": 1}
PRIORITY_ALIAS = {
    "high": "high", "h": "high", "高": "high",
    "normal": "normal", "n": "normal", "中": "normal", "普通": "normal",
    "low": "low", "l": "low", "低": "low",
}
PRIORITY_LABEL = {"high": "高", "normal": "中", "low": "低"}


class CommandError(Exception):
    """命令执行失败。由 main() 统一捕获，转成退出码和错误信息。"""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# 终端颜色
# --------------------------------------------------------------------------
class C:
    """ANSI 颜色。不是在终端里运行（比如输出到文件）时自动关闭。"""

    enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def paint(cls, text: str, *styles: str) -> str:
        if not cls.enabled or not styles:
            return text
        return "".join(styles) + text + cls.RESET


def enable_ansi_on_windows() -> None:
    """Windows 10 之前的控制台默认不解析 ANSI，这里顺手打开（失败就忽略）。"""
    if os.name == "nt":
        try:
            os.system("")
        except Exception:
            pass


# --------------------------------------------------------------------------
# 数据存储
# --------------------------------------------------------------------------
def db_path() -> Path:
    custom = os.environ.get("CLITODO_DB")
    return Path(custom).expanduser() if custom else Path.home() / ".clitodo" / "todos.json"


def load(path: Path) -> dict:
    if not path.exists():
        return {"next_id": 1, "items": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise CommandError(f"读取数据文件失败：{path}\n{exc}")
    data.setdefault("next_id", 1)
    data.setdefault("items", [])
    return data


def save(path: Path, data: dict) -> None:
    """先写临时文件再原子替换，避免写到一半断电把数据写坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def parse_date(text: str | None) -> str | None:
    """把 2026-11-01 / 11-01 / today / tomorrow 统一成 YYYY-MM-DD。"""
    if not text:
        return None
    text = text.strip().lower()
    today = date.today()
    if text in ("today", "今天"):
        return today.isoformat()
    if text in ("tomorrow", "明天"):
        return date.fromordinal(today.toordinal() + 1).isoformat()
    if text in ("none", "-", "无"):
        return None
    parts = text.replace("/", "-").replace(".", "-").split("-")
    try:
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            # 只给了月-日，默认按今年算
            return date(today.year, int(parts[0]), int(parts[1])).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期不存在：{text}")
    raise argparse.ArgumentTypeError(f"无法识别的日期：{text}（示例：2026-11-01 / 11-01 / tomorrow）")


def parse_priority(text: str) -> str:
    try:
        return PRIORITY_ALIAS[text.strip().lower()]
    except KeyError:
        raise argparse.ArgumentTypeError(
            f"未知优先级：{text}（可选 high / normal / low，或 高 / 中 / 低）"
        )


def due_status(due: str | None) -> tuple[str, str]:
    """返回 (展示文本, 颜色)。"""
    if not due:
        return "", ""
    d = date.fromisoformat(due)
    today = date.today()
    if d < today:
        return f"{due} 逾期{ (today - d).days }天", C.RED
    if d == today:
        return f"{due} 今天", C.YELLOW
    if (d - today).days == 1:
        return f"{due} 明天", C.YELLOW
    return f"{due} 剩{(d - today).days}天", C.DIM


def find_item(data: dict, item_id: int) -> dict | None:
    for item in data["items"]:
        if item["id"] == item_id:
            return item
    return None


def parse_ids(values: list[str]) -> list[int]:
    """支持 done 1 2 3-5 这种写法。"""
    ids: list[int] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part[1:]:
                start, end = part.split("-", 1)
                ids.extend(range(int(start), int(end) + 1))
            else:
                ids.append(int(part))
    return ids


def sort_key(item: dict):
    """未完成的在前，然后按优先级、到期日、编号。"""
    due = item.get("due") or "9999-12-31"
    return (item["done"], -PRIORITIES[item["priority"]], due, item["id"])


# --------------------------------------------------------------------------
# 显示
# --------------------------------------------------------------------------
def render_table(items: list[dict]) -> str:
    if not items:
        return C.paint("  （这里空的，一条待办都没有）", C.DIM)

    id_w = max(len("ID"), max(len(str(i["id"])) for i in items))
    title_w = max(len("内容"), max(display_width(i["title"]) for i in items))
    title_w = min(title_w, 46)

    header = (
        f"{'ID':>{id_w}}  {'状态':<4}  {'优先级':<6}  {'内容':<{title_w}}  {'到期'}"
    )
    lines = [C.paint(header, C.BOLD), C.paint("─" * (id_w + title_w + 24), C.DIM)]

    for item in items:
        mark = C.paint("✓", C.GREEN) if item["done"] else " "
        prio = item["priority"]
        prio_text = C.paint(PRIORITY_LABEL[prio], {
            "high": C.RED, "normal": C.CYAN, "low": C.DIM,
        }[prio])
        title = truncate(item["title"], title_w)
        if item["done"]:
            title = C.paint(title, C.DIM)
        due_text, due_color = due_status(item.get("due"))
        if due_color and not item["done"]:
            due_text = C.paint(due_text, due_color)
        lines.append(
            f"{item['id']:>{id_w}}  [{mark}]  {prio_text:<6}  {title:<{title_w}}  {due_text}"
        )
    return "\n".join(lines)


def display_width(text: str) -> int:
    """中文按 2 个字符宽度算，让对齐在终端里好看。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def truncate(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text + " " * (width - display_width(text))
    out, used = "", 0
    for ch in text:
        w = display_width(ch)
        if used + w > width - 1:
            break
        out += ch
        used += w
    return out + "…" + " " * (width - used - 1)


def print_items(items: list[dict]) -> None:
    print(render_table(items))


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_add(args, path: Path) -> None:
    data = load(path)
    item = {
        "id": data["next_id"],
        "title": args.title.strip(),
        "done": False,
        "priority": PRIORITY_ALIAS[args.priority],
        "due": parse_date(args.due),
        "created": datetime.now().isoformat(timespec="seconds"),
        "done_at": None,
    }
    data["items"].append(item)
    data["next_id"] += 1
    save(path, data)
    extra = f"，到期 {item['due']}" if item["due"] else ""
    print(C.paint(f"✓ 已添加 #{item['id']}：{item['title']}", C.GREEN) + C.paint(extra, C.DIM))


def cmd_list(args, path: Path) -> None:
    data = load(path)
    items = data["items"]
    if not args.all:
        items = [i for i in items if not i["done"]]
    if args.priority:
        target = PRIORITY_ALIAS[args.priority]
        items = [i for i in items if i["priority"] == target]
    if args.tag:
        items = [i for i in items if args.tag in i["title"]]
    items = sorted(items, key=sort_key)

    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return

    print_items(items)
    left = sum(1 for i in data["items"] if not i["done"])
    print(C.paint(f"\n  共 {len(items)} 条显示 / 未完成 {left} 条", C.DIM))


def _mark(args, path: Path, done: bool) -> None:
    data = load(path)
    ids = parse_ids(args.ids)
    changed, missing = [], []
    for item_id in ids:
        item = find_item(data, item_id)
        if item is None:
            missing.append(item_id)
            continue
        item["done"] = done
        item["done_at"] = datetime.now().isoformat(timespec="seconds") if done else None
        changed.append(item)
    if changed:
        save(path, data)
    for item in changed:
        verb = "完成" if done else "恢复"
        color = C.GREEN if done else C.YELLOW
        print(C.paint(f"{'✓' if done else '↺'} 已{verb} #{item['id']}：{item['title']}", color))
    if missing:
        raise CommandError(f"找不到编号：{', '.join(map(str, missing))}")


def cmd_done(args, path: Path) -> None:
    _mark(args, path, True)


def cmd_undone(args, path: Path) -> None:
    _mark(args, path, False)


def cmd_rm(args, path: Path) -> None:
    data = load(path)
    ids = set(parse_ids(args.ids))
    keep, removed = [], []
    for item in data["items"]:
        (removed if item["id"] in ids else keep).append(item)
    if not removed:
        raise CommandError(f"找不到编号：{', '.join(map(str, sorted(ids)))}")
    data["items"] = keep
    save(path, data)
    for item in removed:
        print(C.paint(f"✗ 已删除 #{item['id']}：{item['title']}", C.RED))


def cmd_edit(args, path: Path) -> None:
    data = load(path)
    item = find_item(data, args.id)
    if item is None:
        raise CommandError(f"找不到编号：{args.id}")
    if args.title:
        item["title"] = args.title.strip()
    if args.priority:
        item["priority"] = PRIORITY_ALIAS[args.priority]
    if args.due is not None:
        item["due"] = parse_date(args.due)
    save(path, data)
    print(C.paint(f"✎ 已更新 #{item['id']}：{item['title']}", C.BLUE))


def cmd_search(args, path: Path) -> None:
    data = load(path)
    keyword = args.keyword.lower()
    hits = [i for i in data["items"] if keyword in i["title"].lower()]
    if not hits:
        print(C.paint(f"没有找到包含“{args.keyword}”的待办", C.DIM))
        return
    print_items(sorted(hits, key=sort_key))


def cmd_clear(args, path: Path) -> None:
    data = load(path)
    finished = [i for i in data["items"] if i["done"]]
    if not finished:
        print(C.paint("没有已完成的项目需要清理", C.DIM))
        return
    if not args.yes:
        answer = input(f"确认删除 {len(finished)} 条已完成的待办？[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消")
            return
    data["items"] = [i for i in data["items"] if not i["done"]]
    save(path, data)
    print(C.paint(f"✗ 已清理 {len(finished)} 条已完成的待办", C.RED))


def cmd_stats(args, path: Path) -> None:
    data = load(path)
    items = data["items"]
    if not items:
        print(C.paint("还没有任何待办", C.DIM))
        return
    total = len(items)
    done = sum(1 for i in items if i["done"])
    overdue = sum(
        1 for i in items
        if not i["done"] and i.get("due") and date.fromisoformat(i["due"]) < date.today()
    )
    rate = done / total
    bar_len = 24
    filled = int(rate * bar_len)
    bar = C.paint("█" * filled, C.GREEN) + C.paint("░" * (bar_len - filled), C.DIM)
    print(f"  总计 {total}  已完成 {done}  未完成 {total - done}  逾期 {overdue}")
    print(f"  [{bar}] {rate:.0%}")
    for prio in ("high", "normal", "low"):
        count = sum(1 for i in items if i["priority"] == prio and not i["done"])
        print(f"  未完成 · {PRIORITY_LABEL[prio]}优先级：{count}")


def cmd_doctor(args, path: Path) -> None:
    print(f"  数据文件：{path}  {'存在' if path.exists() else '尚未创建'}")
    print(f"  Python：{sys.version.split()[0]}")
    print(f"  终端颜色：{'开启' if C.enabled else '关闭'}")


# --------------------------------------------------------------------------
# 参数解析
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="todo",
        description="clitodo —— 零依赖的命令行待办事项工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "常用示例：\n"
            "  todo add \"买牛奶\" --priority high --due tomorrow\n"
            "  todo ls\n"
            "  todo done 1 3\n"
            "  todo ls --all --json\n"
        ),
    )
    parser.add_argument("-V", "--version", action="version", version=f"clitodo {VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    p_add = sub.add_parser("add", aliases=["a"], help="添加一条待办")
    p_add.add_argument("title", help="待办内容")
    p_add.add_argument("-p", "--priority", default="normal", type=parse_priority,
                       help="优先级：high/normal/low（也可写 高/中/低）")
    p_add.add_argument("-d", "--due", default=None, help="到期日：2026-11-01 / tomorrow / today")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", aliases=["ls", "l"], help="列出待办")
    p_list.add_argument("-a", "--all", action="store_true", help="包含已完成的")
    p_list.add_argument("-p", "--priority", choices=["high", "normal", "low"], help="只看某个优先级")
    p_list.add_argument("-t", "--tag", help="按关键字筛选")
    p_list.add_argument("--json", action="store_true", help="输出 JSON，方便交给别的程序处理")
    p_list.set_defaults(func=cmd_list)

    p_done = sub.add_parser("done", aliases=["d"], help="标记完成")
    p_done.add_argument("ids", nargs="+", help="编号，可写多个或区间：1 3-5")
    p_done.set_defaults(func=cmd_done)

    p_undone = sub.add_parser("undone", aliases=["u"], help="取消完成")
    p_undone.add_argument("ids", nargs="+", help="编号")
    p_undone.set_defaults(func=cmd_undone)

    p_rm = sub.add_parser("rm", help="删除待办")
    p_rm.add_argument("ids", nargs="+", help="编号")
    p_rm.set_defaults(func=cmd_rm)

    p_edit = sub.add_parser("edit", aliases=["e"], help="修改待办")
    p_edit.add_argument("id", type=int, help="编号")
    p_edit.add_argument("title", nargs="?", help="新内容")
    p_edit.add_argument("-p", "--priority", type=parse_priority)
    p_edit.add_argument("-d", "--due", help="新的到期日")
    p_edit.set_defaults(func=cmd_edit)

    p_search = sub.add_parser("search", aliases=["s"], help="搜索待办")
    p_search.add_argument("keyword", help="关键字")
    p_search.set_defaults(func=cmd_search)

    p_clear = sub.add_parser("clear", help="清理已完成的待办")
    p_clear.add_argument("-y", "--yes", action="store_true", help="不再询问，直接删除")
    p_clear.set_defaults(func=cmd_clear)

    p_stats = sub.add_parser("stats", help="统计完成情况")
    p_stats.set_defaults(func=cmd_stats)

    p_doctor = sub.add_parser("doctor", help="检查运行环境与数据文件位置")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    enable_ansi_on_windows()
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help / --version 或参数写错
        return int(exc.code or 0)

    if not getattr(args, "func", None):
        parser.print_help()
        return 0

    try:
        args.func(args, db_path())
    except CommandError as exc:
        print(C.paint(str(exc), C.RED), file=sys.stderr)
        return exc.code
    except argparse.ArgumentTypeError as exc:
        print(C.paint(f"参数错误：{exc}", C.RED), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
