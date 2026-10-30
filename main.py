"""跨会话 @ 人：在私聊里也能把 @ 消息发到指定群。

背景：AtTool 的 `get_group_members` 只在群聊里有效（它读事件里的 group_id），
所以在私聊里说「去群里 @ 我」，模型既拿不到群号也拿不到成员 QQ。
本插件直接走 OneBot action（get_group_list / get_group_member_list / send_group_msg），
把「找到群 → 找到人 → 发一条真 @」这三步做成两个工具。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

CACHE_TTL = 60.0
MAX_LIST_ITEMS = 40


def resolve_event(event):
    """AstrBot 4.26+ 的 LLM 工具会传 ContextWrapper，需要取出真正的消息事件。"""
    current = event
    for _ in range(3):
        if getattr(current, "bot", None) is not None:
            return current
        nxt = getattr(getattr(current, "context", None), "event", None)
        if nxt is None:
            break
        current = nxt
    return current


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._groups: list[dict[str, Any]] = []
        self._groups_at = 0.0
        self._members: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._last_sent: dict[str, float] = {}

    # ------------------------------------------------------------ config
    def _cfg(self, key, default):
        value = (self.config or {}).get(key, default)
        return default if value is None else value

    def _list_cfg(self, key) -> list[str]:
        raw = self._cfg(key, [])
        if isinstance(raw, str):
            return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
        if isinstance(raw, (list, tuple)):
            return [str(x).strip() for x in raw if str(x).strip()]
        return []

    # -------------------------------------------------------------- data
    async def _get_groups(self, event: AstrMessageEvent, force: bool = False) -> list[dict]:
        now = time.monotonic()
        if not force and self._groups and now - self._groups_at < CACHE_TTL:
            return self._groups
        bot = getattr(event, "bot", None)
        if bot is None:
            return []
        try:
            groups = await bot.get_group_list()
        except Exception as exc:
            logger.warning(f"[group-mention] 取群列表失败: {exc}")
            return []
        cleaned = []
        for g in groups or []:
            try:
                cleaned.append(
                    {
                        "group_id": str(g.get("group_id", "")),
                        "group_name": str(g.get("group_name", "") or "").strip(),
                        "member_count": g.get("member_count") or g.get("max_member_count") or 0,
                    }
                )
            except Exception:
                continue
        allow = set(self._list_cfg("allowed_groups"))
        if allow:
            cleaned = [g for g in cleaned if g["group_id"] in allow]
        self._groups, self._groups_at = cleaned, now
        return cleaned

    async def _get_members(self, event: AstrMessageEvent, group_id: str) -> list[dict]:
        cached = self._members.get(group_id)
        now = time.monotonic()
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]
        bot = getattr(event, "bot", None)
        if bot is None:
            return []
        try:
            members = await bot.get_group_member_list(group_id=int(group_id))
        except Exception as exc:
            logger.warning(f"[group-mention] 取群 {group_id} 成员失败: {exc}")
            return []
        rows = []
        for m in members or []:
            rows.append(
                {
                    "user_id": str(m.get("user_id", "")),
                    "nickname": str(m.get("nickname", "") or "").strip(),
                    "card": str(m.get("card", "") or "").strip(),
                }
            )
        self._members[group_id] = (now, rows)
        return rows

    @staticmethod
    def _pick(name: str, rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
        """按子串匹配；命中多个时返回全部，交给模型确认。"""
        key = name.strip().lower()
        if not key:
            return []
        exact = [r for r in rows if any(str(r.get(f, "")).lower() == key for f in fields)]
        if exact:
            return exact
        return [r for r in rows if any(key in str(r.get(f, "")).lower() for f in fields)]

    # ------------------------------------------------------------- tools
    @filter.llm_tool(name="list_groups")
    async def list_groups(self, event: AstrMessageEvent):
        """列出机器人所在的群（群号、群名、人数）。

        在私聊里想「去某个群 @ 某人」时，先用它确认群号/群名，再用 send_group_mention 发送。
        """
        event = resolve_event(event)
        groups = await self._get_groups(event, force=True)
        if not groups:
            yield "没查到群列表（可能当前平台不支持，或机器人不在任何群里）。"
            return
        lines = [f"{g['group_id']} | {g['group_name'] or '（未命名）'} | {g['member_count']}人" for g in groups[:MAX_LIST_ITEMS]]
        more = "" if len(groups) <= MAX_LIST_ITEMS else f"\n（还有 {len(groups) - MAX_LIST_ITEMS} 个群未列出）"
        yield "群号 | 群名 | 人数：\n" + "\n".join(lines) + more

    @filter.llm_tool(name="send_group_mention")
    async def send_group_mention(
        self,
        event: AstrMessageEvent,
        group: str,
        who: str,
        text: str = "",
    ):
        """往指定群发一条 @ 某个成员的消息（私聊里也能用）。

        适合：用户在私聊里说「去群里 @ 我」「在 XX 群提醒 YY」。
        会先按群号或群名找人，再按 QQ 号、群名片、昵称找人，都确认唯一后才发送。

        Args:
            group(string): 群号（纯数字）或群名的一部分。
            who(string): 要 @ 的人：QQ 号、群名片或昵称的一部分；填「我」表示当前说话的人。
            text(string): @ 之后要说的内容，尽量短。
        """
        event = resolve_event(event)
        enabled = bool(self._cfg("enable", True))
        if not enabled:
            yield "跨群 @ 功能已关闭。"
            return

        text = str(text or "").strip()
        limit = int(self._cfg("max_text_chars", 120) or 120)
        if len(text) > limit:
            text = text[:limit]

        groups = await self._get_groups(event)
        if not groups:
            yield "查不到群列表，暂时没法发到群里。"
            return

        # 1) 定位群
        key = str(group or "").strip()
        group_hit = [g for g in groups if g["group_id"] == key]
        if not group_hit:
            group_hit = self._pick(key, groups, ("group_name",))
        if not group_hit:
            yield f"没找到群「{key}」。可以先用 list_groups 看一下群名。"
            return
        if len(group_hit) > 1:
            names = "、".join(f"{g['group_name']}({g['group_id']})" for g in group_hit[:5])
            yield f"「{key}」匹配到多个群：{names}。请用群号指定。"
            return
        target_group = group_hit[0]
        gid = target_group["group_id"]

        # 2) 定位人
        members = await self._get_members(event, gid)
        if not members:
            yield f"拿不到群 {gid} 的成员列表，没法确认要 @ 谁。"
            return
        who_key = str(who or "").strip()
        if who_key in ("我", "自己", "本人", "me", "Me"):
            who_key = str(event.get_sender_id())
        person_hit = [m for m in members if m["user_id"] == who_key]
        if not person_hit:
            person_hit = self._pick(who_key, members, ("card", "nickname"))
        if not person_hit:
            sample = "、".join(
                f"{(m['card'] or m['nickname'] or '?')}({m['user_id']})" for m in members[:8]
            )
            yield f"群里没找到「{who_key}」。群成员示例：{sample}"
            return
        if len(person_hit) > 1:
            names = "、".join(f"{(m['card'] or m['nickname'] or '?')}({m['user_id']})" for m in person_hit[:5])
            yield f"「{who_key}」匹配到多个人：{names}。请用 QQ 号指定。"
            return
        target = person_hit[0]

        # 3) 限流
        cooldown = float(self._cfg("cooldown_seconds", 15) or 0)
        if time.monotonic() - self._last_sent.get(gid, 0.0) < cooldown:
            yield "刚刚往这个群发过消息，稍等一下再发。"
            return

        bot = getattr(event, "bot", None)
        if bot is None:
            yield "当前平台不支持直接发群消息。"
            return
        message = [{"type": "at", "data": {"qq": target["user_id"]}}]
        if text:
            message.append({"type": "text", "data": {"text": " " + text}})
        try:
            await bot.send_group_msg(group_id=int(gid), message=message)
        except Exception as exc:
            logger.warning(f"[group-mention] 发送失败: {exc}")
            yield f"发送失败：{type(exc).__name__}"
            return
        self._last_sent[gid] = time.monotonic()
        shown = target["card"] or target["nickname"] or target["user_id"]
        yield f"已在「{target_group['group_name'] or gid}」@ 了 {shown}。"
