#!/usr/bin/env python3
"""hsreview: Hearthstone の Power.log から、振り返り用の棋譜(Markdown)を作る。

使い方:
  python hsreview.py sync          # ログから未取り込みのランク戦(スタンダード)を games/ に保存
  python hsreview.py list          # 保存済みの試合一覧
  python hsreview.py show latest   # 棋譜を表示 (ID指定 / latest / 一覧の番号)

作った games/<ID>.md を Claude に渡して「この試合を振り返って」と頼む。
ゲームのログファイルを読むだけで、ゲーム本体には一切触れない。
"""
import argparse
import gzip
import io
import json
import logging
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from hearthstone.enums import BlockType, CardType, ChoiceType, GameTag, PlayState, Step, Zone
from hslog import LogParser
from hslog.export import EntityTreeExporter
from hslog.player import PlayerReference

logging.disable(logging.WARNING)  # hslog の "Broken option nesting" 警告を抑える

HERE = Path(__file__).resolve().parent
GAMES_DIR = HERE / "games"
CARDS_DIR = HERE / "cards"
KNOW_DIR = HERE / "knowledge"
DEFAULT_LOGS = Path(r"C:\Program Files (x86)\Hearthstone\Logs")
LOCALE = "jaJP"

CLASS_JA = {
    "DEATHKNIGHT": "デスナイト", "DEMONHUNTER": "デーモンハンター", "DRUID": "ドルイド",
    "HUNTER": "ハンター", "MAGE": "メイジ", "PALADIN": "パラディン", "PRIEST": "プリースト",
    "ROGUE": "ローグ", "SHAMAN": "シャーマン", "WARLOCK": "ウォーロック", "WARRIOR": "ウォリアー",
}
KEYWORDS = [
    (GameTag.TAUNT, "挑発"), (GameTag.DIVINE_SHIELD, "聖なる盾"), (GameTag.STEALTH, "隠密"),
    (GameTag.POISONOUS, "猛毒"), (GameTag.WINDFURY, "疾風"), (GameTag.LIFESTEAL, "吸血"),
    (GameTag.CHARGE, "突撃"), (GameTag.RUSH, "速攻"), (GameTag.REBORN, "復活"),
    (GameTag.FROZEN, "凍結"), (GameTag.DORMANT, "休眠"), (GameTag.IMMUNE, "無敵"),
]


# ---------------------------------------------------------------- カード辞典
class Cards:
    """HearthstoneJSON のカードデータ(日本語)。初回だけダウンロードして cards/ に保存。"""

    def __init__(self, build):
        CARDS_DIR.mkdir(exist_ok=True)
        path = CARDS_DIR / f"{build}_{LOCALE}.json"
        if not path.exists():
            self._download(build, path)
        self.by_id = {c["id"]: c for c in json.loads(path.read_text(encoding="utf-8"))}
        self.used = {}  # 棋譜に登場したカード

    @staticmethod
    def _download(build, path):
        last = None
        for ver in (build, "latest"):
            url = f"https://api.hearthstonejson.com/v1/{ver}/{LOCALE}/cards.json"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "hsreview"})
                data = urllib.request.urlopen(req, timeout=60).read()
                path.write_bytes(data)
                print(f"カードデータを取得: {url}", file=sys.stderr)
                return
            except Exception as ex:  # noqa: BLE001
                last = ex
        raise SystemExit(f"カードデータを取得できませんでした: {last}")

    def name(self, card_id):
        if not card_id:
            return "(不明なカード)"
        c = self.by_id.get(card_id)
        if not c:
            return card_id
        self.used[card_id] = c
        return c.get("name", card_id)

    def klass(self, card_id):
        return (self.by_id.get(card_id) or {}).get("cardClass", "")


def load_knowledge():
    """knowledge/keywords.json: {"仕込み": "定義..."}, knowledge/sets.json: {"SET_CODE": "日本語名(発売日)"}"""
    out = {}
    for name in ("keywords", "sets"):
        p = KNOW_DIR / f"{name}.json"
        out[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return out


BUILTIN_KW = {"挑発", "雄叫び", "断末魔", "聖なる盾", "疾風", "隠密", "生命奪取", "突撃", "速攻", "蘇り", "猛毒", "秘策",
              "発見", "凍結", "沈黙", "呪文ダメージ", "秘密", "クエスト", "報酬", "交換可", "休眠", "超大型"}


def bold_terms(text):
    out = set()
    for t in re.findall(r"<b>(.*?)</b>", text or ""):
        t = re.sub(r"[+＋]\d+|[:：]", "", re.sub(r"<[^>]+>", "", t)).strip(" 、,. ")
        if 1 < len(t) <= 8:
            out.add(t)
    return out


def clean_text(t):
    t = re.sub(r"<[^>]+>", "", t or "")
    t = t.replace("\n", " ").replace("[x]", "").replace("$", "").replace("#", "")
    return t.strip()


# ---------------------------------------------------------------- 棋譜の記録
class Recorder(EntityTreeExporter):
    def __init__(self, tree, me_pid, cards):
        super().__init__(tree, tolerate_missing_entities=True)
        self.me = me_pid
        self.cards = cards
        self.events = []          # [turn, text] 。textは後で埋まることがある
        self.turn = 0
        self.turn_slot = None
        self.snap_done_turn = -1
        self.seen_hand = set()    # これまで手札に入ったことのあるentity id
        self.my_turn_count = 0
        self.errors = 0
        self.mull_offered = {}    # entity id -> name
        self.my_class = ""
        self.opp_class = ""
        self.first_player_is_me = None
        self.turn_stats = []      # 自分の各ターンの統計
        self.cur = None
        self.hp_log = []          # (turn, 自HP, 相手HP)
        self.coin_turn = None     # コインを使った(自分の)ターン番号

    # ---- 例外に強くする
    def export_packet(self, packet):
        try:
            super().export_packet(packet)
        except Exception:  # noqa: BLE001
            self.errors += 1

    # ---- ヘルパ
    def ent(self, eid):
        try:
            return self.game.find_entity_by_id(int(eid))
        except Exception:  # noqa: BLE001
            return None

    def ctrl(self, e):
        return e.tags.get(GameTag.CONTROLLER)

    def side(self, e):
        return "[自]" if self.ctrl(e) == self.me else "[敵]"

    def name_of(self, e):
        return self.cards.name(getattr(e, "card_id", None)) if e else "(不明)"

    def stat(self, e):
        atk = e.tags.get(GameTag.ATK, 0)
        hp = e.tags.get(GameTag.HEALTH, 0) - e.tags.get(GameTag.DAMAGE, 0)
        kws = [ja for tag, ja in KEYWORDS if e.tags.get(tag)]
        return f"{atk}/{hp}" + ("" if not kws else " " + ",".join(kws))

    def label(self, e):
        if e is None:
            return "(不明)"
        t = e.tags.get(GameTag.CARDTYPE)
        if t == CardType.HERO:
            return f"{self.side(e)}ヒーロー({self.name_of(e)}, HP{self.hp(e)})"
        if t in (CardType.MINION,):
            return f"{self.side(e)}{self.name_of(e)}({self.stat(e)})"
        return f"{self.side(e)}{self.name_of(e)}"

    def hp(self, hero):
        return hero.tags.get(GameTag.HEALTH, 0) - hero.tags.get(GameTag.DAMAGE, 0) + hero.tags.get(GameTag.ARMOR, 0)

    def zone_ents(self, pid, zone, types=None):
        out = [
            e for e in self.game.entities
            if e.tags.get(GameTag.CONTROLLER) == pid and e.tags.get(GameTag.ZONE) == zone
            and (types is None or e.tags.get(GameTag.CARDTYPE) in types)
        ]
        return sorted(out, key=lambda e: e.tags.get(GameTag.ZONE_POSITION, 0))

    def player(self, pid):
        for p in self.game.players:
            if p.player_id == pid:
                return p
        return None

    def opp_pid(self):
        return 2 if self.me == 1 else 1

    def mana_left(self):
        p = self.player(self.me)
        t = p.tags
        return max(0, t.get(GameTag.RESOURCES, 0) - t.get(GameTag.OVERLOAD_LOCKED, 0)
                   - t.get(GameTag.RESOURCES_USED, 0) + t.get(GameTag.TEMP_RESOURCES, 0))

    def hero_hp(self, pid):
        p = self.player(pid)
        hero = self.ent(p.tags.get(GameTag.HERO_ENTITY, 0)) if p else None
        return self.hp(hero) if hero else None

    def hero_line(self, pid):
        p = self.player(pid)
        hero = self.ent(p.tags.get(GameTag.HERO_ENTITY, 0)) if p else None
        if hero is None:
            return "ヒーロー不明"
        s = f"HP{hero.tags.get(GameTag.HEALTH, 0) - hero.tags.get(GameTag.DAMAGE, 0)}"
        if hero.tags.get(GameTag.ARMOR):
            s += f"+護甲{hero.tags[GameTag.ARMOR]}"
        weapons = self.zone_ents(pid, Zone.PLAY, (CardType.WEAPON,))
        if weapons:
            w = weapons[0]
            s += f" 武器:{self.name_of(w)}({w.tags.get(GameTag.ATK, 0)}/{w.tags.get(GameTag.DURABILITY, 0) - w.tags.get(GameTag.DAMAGE, 0)})"
        return s

    def board_line(self, pid):
        ms = self.zone_ents(pid, Zone.PLAY, (CardType.MINION, CardType.LOCATION))
        return "、".join(f"{self.name_of(e)}({self.stat(e)})" if e.tags.get(GameTag.CARDTYPE) == CardType.MINION
                        else f"{self.name_of(e)}(場所)" for e in ms) or "なし"

    def secrets_line(self, pid):
        ss = self.zone_ents(pid, Zone.SECRET)
        if not ss:
            return ""
        if pid == self.me:
            return "、".join(self.name_of(e) for e in ss)
        return f"{len(ss)}枚(秘策)"

    # ---- スナップショット
    def snapshot_start(self):
        me, op = self.me, self.opp_pid()
        p = self.player(me)
        total = p.tags.get(GameTag.RESOURCES, 0) + p.tags.get(GameTag.TEMP_RESOURCES, 0)
        used = p.tags.get(GameTag.RESOURCES_USED, 0)
        hand = self.zone_ents(me, Zone.HAND)
        hs = []
        for e in hand:
            mark = "" if e.id in self.seen_hand else "*"
            self.seen_hand.add(e.id)
            hs.append(f"{mark}{self.name_of(e)}[{e.tags.get(GameTag.COST, 0)}]")
        lines = [
            f"  マナ: {total - used}/{p.tags.get(GameTag.RESOURCES, 0)}",
            f"  手札({len(hand)}): " + "、".join(hs) + "   (*は今ターン新しく手札に入ったカード)",
            f"  自分: {self.hero_line(me)} / 場: {self.board_line(me)}"
            + (f" / 秘策: {self.secrets_line(me)}" if self.secrets_line(me) else "")
            + f" / デッキ残り{len(self.zone_ents(me, Zone.DECK))}枚",
            f"  相手: {self.hero_line(op)} / 場: {self.board_line(op)}"
            + (f" / 秘策: {self.secrets_line(op)}" if self.secrets_line(op) else "")
            + f" / 手札{len(self.zone_ents(op, Zone.HAND))}枚",
        ]
        return "\n".join(lines)

    def snapshot_end(self, mine):
        me, op = self.me, self.opp_pid()
        hand = self.zone_ents(me, Zone.HAND)
        lines = [
            f"  [ターン終了時] 自分: {self.hero_line(me)} / 場: {self.board_line(me)}"
            + (f" / 秘策: {self.secrets_line(me)}" if self.secrets_line(me) else ""),
            f"                 相手: {self.hero_line(op)} / 場: {self.board_line(op)}"
            + (f" / 秘策: {self.secrets_line(op)}" if self.secrets_line(op) else ""),
        ]
        if mine:
            lines.append("                 手札に残したカード: " + ("、".join(
                f"{self.name_of(e)}[{e.tags.get(GameTag.COST, 0)}]" for e in hand) or "なし"))
        else:
            lines.append(f"                 相手の手札: {len(self.zone_ents(op, Zone.HAND))}枚 / 自分の手札: {len(hand)}枚")
        return "\n".join(lines)

    def add(self, text):
        slot = [self.turn, text]
        self.events.append(slot)
        return slot

    # ---- パケット処理
    def handle_block(self, packet):
        t = packet.type
        slot = None
        pre = {}
        if t in (BlockType.PLAY, BlockType.ATTACK):
            src = self.ent(packet.entity)
            if True:
                slot = self.add(None)
                pre["src_label"] = self.label(src) if src else "(不明)"
                pre["cost"] = src.tags.get(GameTag.COST) if src else None
                tgt = self.ent(packet.target) if packet.target else None
                pre["tgt_label"] = self.label(tgt) if tgt else None
                pre["mine"] = bool(src and self.ctrl(src) == self.me)
                pre["activate"] = bool(src and src.tags.get(GameTag.CARDTYPE) == CardType.LOCATION
                                       and src.tags.get(GameTag.ZONE) == Zone.PLAY)
        super().handle_block(packet)
        if slot is None:
            return
        src = self.ent(packet.entity)
        who = "自分" if pre["mine"] else "相手"
        tgt = f" → 対象 {pre['tgt_label']}" if pre["tgt_label"] else ""
        if t == BlockType.ATTACK:
            slot[1] = f"- {who}の攻撃: {pre['src_label']} → {pre['tgt_label'] or '(ランダムな敵/効果による攻撃)'}"
        else:
            ctype = src.tags.get(GameTag.CARDTYPE) if src else None
            kind = {CardType.MINION: "ミニオン", CardType.SPELL: "スペル", CardType.WEAPON: "武器",
                    CardType.LOCATION: "場所", CardType.HERO: "ヒーロー",
                    CardType.HERO_POWER: "ヒーローパワー"}.get(ctype, "カード")
            cost = pre["cost"] if pre["cost"] is not None else (src.tags.get(GameTag.COST) if src else None)
            if pre["activate"]:
                slot[1] = f"- {who}が場所「{self.name_of(src)}」を起動{tgt}"
            else:
                slot[1] = f"- {who}が{kind}「{self.name_of(src)}」を使用 (コスト{'?' if cost is None else cost}){tgt}"
            if pre["mine"] and self.cur is not None:
                if ctype == CardType.HERO_POWER:
                    self.cur["hp"] += 1
                elif not pre["activate"]:
                    self.cur["plays"].append(self.name_of(src))
                    if getattr(src, "card_id", "").startswith("GAME_005") or self.name_of(src) in ("コイン", "The Coin"):
                        self.coin_turn = self.my_turn_count

    def handle_tag_change(self, packet):
        e = self.ent(packet.entity) if not isinstance(packet.entity, PlayerReference) else None
        old_zone = e.tags.get(GameTag.ZONE) if e is not None else None
        r = super().handle_tag_change(packet)
        if e is None:
            return r
        tag, val = int(packet.tag), packet.value
        if e is self.game:
            if tag == int(GameTag.TURN):
                self.turn = int(val)
                self.turn_slot = self.add(None)
            elif tag == int(GameTag.STEP):
                if int(val) == int(Step.MAIN_ACTION) and self.snap_done_turn != self.turn:
                    self.snap_done_turn = self.turn
                    cur = next((p.player_id for p in self.game.players if p.tags.get(GameTag.CURRENT_PLAYER)), None)
                    mine = cur == self.me
                    if mine:
                        self.my_turn_count += 1
                    if self.turn_slot is not None:
                        self.turn_slot[1] = (f"\n### ターン{self.turn} — {'自分' if mine else '相手'}の手番"
                                             f"（{'自分' if mine else '相手'}の{(self.turn + 1) // 2}回目）")
                    if mine:
                        self.add(self.snapshot_start())
                        self.cur = {"turn": self.turn, "n": self.my_turn_count, "avail": self.mana_left(),
                                    "hp": 0, "plays": [],
                                    "costs": {e.id: e.tags.get(GameTag.COST, 0) for e in self.zone_ents(self.me, Zone.HAND)}}
                    self._cur_mine = mine
                elif int(val) == int(Step.MAIN_END):
                    mine = getattr(self, "_cur_mine", False)
                    self.add(self.snapshot_end(mine))
                    self.hp_log.append((self.turn, self.hero_hp(self.me), self.hero_hp(self.opp_pid())))
                    if mine and self.cur is not None:
                        self.finish_my_turn()
        elif tag == int(GameTag.ZONE):
            ctype = e.tags.get(GameTag.CARDTYPE)
            if old_zone == Zone.SECRET and val == Zone.GRAVEYARD:
                self.add(f"- 秘策が発動/消滅: {self.side(e)}{self.name_of(e)}")
            elif old_zone == Zone.PLAY and val == Zone.GRAVEYARD and ctype in (CardType.MINION, CardType.WEAPON, CardType.LOCATION):
                self.add(f"- 場から破壊/死亡: {self.side(e)}{self.name_of(e)}")
            elif old_zone == Zone.PLAY and val == Zone.HAND and ctype == CardType.MINION:
                self.add(f"- 手札に戻った: {self.side(e)}{self.name_of(e)}")
        return r

    def finish_my_turn(self, final=False):
        c = self.cur
        c["left"] = self.mana_left()
        hand = self.zone_ents(self.me, Zone.HAND)
        c["hand_n"] = len(hand)
        c["castable"] = [self.name_of(e) for e in hand
                         if e.tags.get(GameTag.COST, 99) <= c["avail"]
                         and e.tags.get(GameTag.CARDTYPE) != CardType.HERO_POWER]
        # 仕込み: 残りマナを使ってカードのコストを下げた場合は「余り」ではなく使用済みとして扱う
        prep = []
        for e in hand:
            info = self.cards.by_id.get(getattr(e, "card_id", None)) or {}
            if "仕込み" in (info.get("text") or ""):
                after = e.tags.get(GameTag.COST, 0)
                before = c["costs"].get(e.id, info.get("cost", after))  # ターン中に手札へ入ったカードは元のコストと比べる
                if before - after > 0:
                    prep.append(f"{self.name_of(e)}({before}→{after})")
        c["prep"] = prep
        c["left_eff"] = 0 if prep else c["left"]
        c["final"] = final
        self.turn_stats.append(c)
        self.cur = None

    def flush(self):
        """ゲームが手番の途中で終わった場合、最終ターンの統計も残す。"""
        super().flush()
        if self.cur is not None:
            self.finish_my_turn(final=True)
            self.hp_log.append((self.turn, self.hero_hp(self.me), self.hero_hp(self.opp_pid())))

    def handle_choices(self, packet):
        super().handle_choices(packet)
        pid = getattr(packet.entity, "player_id", None)
        if pid is None:
            p = self.ent(packet.entity) if not isinstance(packet.entity, PlayerReference) else None
            pid = getattr(p, "player_id", None)
        if pid != self.me:
            return
        names = [(c, self.name_of(self.ent(c))) for c in packet.choices]
        self._mulligan_open = packet.type == ChoiceType.MULLIGAN
        if self._mulligan_open:
            self.mull_offered = dict(names)
        else:
            self.add(f"- 自分の選択肢(発見など): " + " / ".join(n for _, n in names))

    def handle_chosen_entities(self, packet):
        super().handle_chosen_entities(packet)
        pid = getattr(packet.entity, "player_id", None)
        if pid is None:
            p = self.ent(packet.entity) if not isinstance(packet.entity, PlayerReference) else None
            pid = getattr(p, "player_id", None)
        if pid == self.me and getattr(self, "_mulligan_open", False):
            self.mull_kept = [self.name_of(self.ent(c)) for c in packet.choices]
            self._mulligan_open = False
        elif pid == self.me:
            self.add("  → 選んだもの: " + " / ".join(self.name_of(self.ent(c)) for c in packet.choices))


# ---------------------------------------------------------------- ログ→試合
SKIP_NAME = "UNKNOWN HUMAN PLAYER"


def split_games(power_log):
    raw = power_log.read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [i for i, l in enumerate(raw) if "GameState.DebugPrintPower() - CREATE_GAME" in l]
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(raw)
        yield raw[s:e]


def game_header(lines):
    h = {"names": {}}
    for l in lines[:900]:
        m = re.search(r"DebugPrintGame\(\) - (\w+)=(.*)$", l)
        if m and m.group(1) in ("GameType", "FormatType", "BuildNumber"):
            h[m.group(1)] = m.group(2).strip()
        m = re.search(r"DebugPrintGame\(\) - PlayerID=(\d+), PlayerName=(.*)$", l)
        if m:
            h["names"][int(m.group(1))] = m.group(2).strip()
    return h


def time_of(line):
    m = re.match(r"\w (\d\d):(\d\d):(\d\d)", line)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def build_record(lines, header, start_dt, me_name=None):
    names = header["names"]
    me = None
    for pid, nm in names.items():
        if (me_name and nm == me_name) or (not me_name and nm != SKIP_NAME):
            me = pid
    if me is None:
        raise ValueError("自分のプレイヤーIDを特定できませんでした")
    cards = Cards(header.get("BuildNumber", "latest"))
    p = LogParser()
    p.read(io.StringIO("\n".join(lines)))
    p.flush()
    if not p.games:
        raise ValueError("ゲームを解析できませんでした")
    rec = Recorder(p.games[0], me, cards)
    # 先攻判定を先に済ませるため、最初のCREATE_GAMEのタグを参照
    rec.export()
    game = rec.game
    mine = rec.player(me)
    oppp = rec.player(rec.opp_pid())
    state = mine.tags.get(GameTag.PLAYSTATE)
    opp_state = oppp.tags.get(GameTag.PLAYSTATE)
    result = {PlayState.WON: "勝ち", PlayState.LOST: "負け", PlayState.TIED: "引き分け",
              PlayState.CONCEDED: "投了(負け)"}.get(state)
    if state == PlayState.CONCEDED or result is None:
        if opp_state == PlayState.CONCEDED:
            result = "相手が投了(勝ち)"
    complete = result is not None
    result = result or "不明(未完了)"
    first_me = bool(mine.tags.get(GameTag.FIRST_PLAYER))

    def hero_of(pl):
        # 初期ヒーロー(プレイヤーのCLASSではなくヒーローカードから推定)
        h = rec.ent(pl.initial_hero_entity_id)
        cls = h.tags.get(GameTag.CLASS) if h else None
        card = h.card_id if h else ""
        cname = rec.cards.klass(card)
        return rec.cards.name(card), CLASS_JA.get(cname, cname or "?")

    my_hero, my_cls = hero_of(mine)
    op_hero, op_cls = hero_of(oppp)

    # 登場カード
    my_cards, opp_cards = {}, {}
    for e in game.entities:
        cid = getattr(e, "card_id", None)
        if not cid:
            continue
        t = e.tags.get(GameTag.CARDTYPE)
        if t in (CardType.MINION, CardType.SPELL, CardType.WEAPON, CardType.LOCATION):
            if e.tags.get(GameTag.CREATOR) or e.tags.get(GameTag.DISPLAYED_CREATOR):
                continue  # 他のカードの効果で生成されたもの
            d = my_cards if e.tags.get(GameTag.CONTROLLER) == me else opp_cards
            d[rec.cards.name(cid)] = d.get(rec.cards.name(cid), 0) + 1

    header_lines = [
        f"# 試合 {start_dt:%Y-%m-%d %H:%M}  ランク戦(スタンダード)",
        "",
        f"- 結果: **{result}**",
        f"- 自分: {my_hero} ({my_cls}) / 相手: {op_hero} ({op_cls})",
        f"- 手番: 自分は{'先攻' if first_me else '後攻'} / 総ターン数: {game.tags.get(GameTag.TURN)}",
        f"- ログ解析で読み飛ばした箇所: {rec.errors}件" + ("（多い場合は棋譜が欠けている可能性）" if rec.errors else ""),
        f"- マリガン: 提示 {'、'.join(rec.mull_offered.values()) or '不明'}"
        f" → キープ {'、'.join(getattr(rec, 'mull_kept', [])) or 'なし(全交換)'}",
        "- このデッキで確認できたカード(生成物を除く): "
        + "、".join(f"{n}×{c}" if c > 1 else n for n, c in sorted(my_cards.items())),
        "- 相手が見せたカード(生成物を除く): "
        + ("、".join(f"{n}×{c}" if c > 1 else n for n, c in sorted(opp_cards.items())) or "なし"),
        "",
        "注意: ログには自分に見えていた情報しか残りません。相手の手札・デッキ内容は、使用された/公開されたカードだけが分かります。",
        "",
        "## 棋譜",
    ]
    body = []
    for slot in rec.events:
        text = slot[1]
        if text:
            body.append(text)
    # 統計
    stats = ["", "## 統計(自動集計)", "",
             "| 自分のターン | 総マナ | 余りマナ | 仕込み | ヒーローパワー | 使ったカード | 総マナ内で出せたのに手札に残したカード |",
             "|---|---|---|---|---|---|---|"]
    for c in rec.turn_stats:
        stats.append(f"| {c['n']}回目(T{c['turn']}{'・最終' if c.get('final') else ''}) | {c['avail']} | {c['left_eff']} | {'、'.join(c['prep']) or '-'} | {'使用' if c['hp'] else '-'} | "
                     f"{'、'.join(c['plays']) or '-'} | {'、'.join(c['castable']) or '-'} |")
    full = [c for c in rec.turn_stats if not c.get("final")]  # 途中終了(投了/決着)のターンは集計から除く
    unspent = sum(c["left_eff"] for c in full)
    hp_uses = sum(c["hp"] for c in rec.turn_stats)
    stats += ["",
              f"- 余りマナ合計: {unspent} / 総マナ合計: {sum(c['avail'] for c in full)}(最終ターンが途中終了の場合は除く)"
              f" / ヒーローパワー使用: {hp_uses}回 / コインを使ったターン: "
              + (f"自分の{rec.coin_turn}回目" if rec.coin_turn else "なし"),
              "- 「出せたのに残したカード」は目安です(温存が正解の場合もあります)。",
              "- HP推移(ターン終了時, 自分/相手): "
              + " ".join(f"T{t}:{a}/{b}" for t, a, b in rec.hp_log)]
    # セットとキーワード
    know = load_knowledge()
    sets = {}
    for cid, c in rec.cards.used.items():
        sets.setdefault(c.get("set", "?"), []).append(c.get("name"))
    kws = {}
    for cid, c in rec.cards.used.items():
        for k, d in know["keywords"].items():
            if k in (c.get("text") or "") and k not in kws:
                kws[k] = d
    seen = set()
    for c in rec.cards.used.values():
        seen |= bold_terms(c.get("text"))
    unknown = sorted(t for t in seen if t not in BUILTIN_KW and t not in know["keywords"])
    meta = ["", "## 登場カードのセットとキーワード(参照用)", ""]
    for code, names in sorted(sets.items()):
        meta.append(f"- {know['sets'].get(code, code)}: {'、'.join(sorted(set(names)))}")
    if kws:
        meta.append("")
        meta += [f"- **{k}**: {d}" for k, d in kws.items()]
    if unknown:
        meta += ["", f"- 未登録の用語(要確認): {'、'.join(unknown)}"]
    # カード辞典
    dic = ["", "## 登場したカードの効果(参照用)"]
    for cid, c in sorted(rec.cards.used.items(), key=lambda kv: kv[1].get("name", "")):
        t = c.get("type", "")
        if t in ("HERO", "ENCHANTMENT", "PLAYER", "GAME"):
            continue
        st = ""
        if t == "MINION":
            st = f" {c.get('attack', 0)}/{c.get('health', 0)}"
        elif t == "WEAPON":
            st = f" {c.get('attack', 0)}/{c.get('durability', 0)}"
        dic.append(f"- {c.get('name')} [{c.get('cost', '-')}マナ {t}{st}]: {clean_text(c.get('text'))}")
    md = chr(10).join(header_lines[:-1] + stats + ["", "## 棋譜"] + body + meta + dic) + chr(10)
    summary = {
        "result": result, "complete": complete, "my": f"{my_hero}({my_cls})", "opp": f"{op_hero}({op_cls})",
        "first": first_me, "turns": game.tags.get(GameTag.TURN), "errors": rec.errors,
        "date": f"{start_dt:%Y-%m-%d %H:%M}",
        "unspent": unspent, "avail": sum(c["avail"] for c in full), "hero_power": hp_uses,
        "coin_turn": rec.coin_turn, "my_turns": len(rec.turn_stats),
        "hp_log": rec.hp_log,
    }
    return md, summary


def cmd_sync(args):
    GAMES_DIR.mkdir(exist_ok=True)
    index_path = GAMES_DIR / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    root = Path(args.logs)
    folders = sorted(root.glob("Hearthstone_*"))
    new = 0
    for folder in folders:
        power = folder / "Power.log"
        if not power.exists():
            continue
        m = re.match(r"Hearthstone_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)", folder.name)
        base = datetime(*map(int, m.groups())) if m else datetime.now()
        prev = base
        for lines in split_games(power):
            t = time_of(lines[0])
            if t:
                dt = prev.replace(hour=t[0], minute=t[1], second=t[2], microsecond=0)
                while dt < prev - timedelta(minutes=1):
                    dt += timedelta(days=1)
                prev = dt
            else:
                dt = prev
            gid = f"{dt:%Y%m%d-%H%M%S}"
            if gid in index:
                continue
            h = game_header(lines)
            if h.get("GameType") != args.gametype or h.get("FormatType") != args.format:
                continue
            try:
                md, summary = build_record(lines, h, dt, args.me)
            except Exception as ex:  # noqa: BLE001
                print(f"[skip] {gid}: {type(ex).__name__}: {ex}", file=sys.stderr)
                continue
            if not summary["complete"] and not args.include_incomplete:
                print(f"[skip] {gid}: 終局まで記録されていません(進行中/切断)", file=sys.stderr)
                continue
            (GAMES_DIR / f"{gid}.md").write_text(md, encoding="utf-8")
            with gzip.open(GAMES_DIR / f"{gid}.log.gz", "wt", encoding="utf-8") as f:
                f.write("\n".join(lines))
            index[gid] = summary
            new += 1
            print(f"[new] {gid} {summary['result']} {summary['my']} vs {summary['opp']}")
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"取り込み: {new}試合 / 保存済み合計: {len(index)}試合")


def cmd_rebuild(_args):
    """保存済みの生ログ(games/*.log.gz)から、棋譜と統計を作り直す(Hearthstone側のログが消えていても可)。"""
    idx = load_index()
    for gid in sorted(idx):
        with gzip.open(GAMES_DIR / f"{gid}.log.gz", "rt", encoding="utf-8") as f:
            lines = f.read().split(chr(10))
        dt = datetime.strptime(gid, "%Y%m%d-%H%M%S")
        h = game_header(lines)
        try:
            md, summary = build_record(lines, h, dt, _args.me)
        except Exception as ex:  # noqa: BLE001
            print(f"[skip] {gid}: {type(ex).__name__}: {ex}", file=sys.stderr)
            continue
        (GAMES_DIR / f"{gid}.md").write_text(md, encoding="utf-8")
        idx[gid] = summary
        print(f"[rebuilt] {gid} {summary['result']}")
    (GAMES_DIR / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_stats(_args):
    idx = load_index()
    print("ID               結果  自分 vs 相手                              余りマナ/総マナ  HP使用  コイン")
    agg = {"勝": [0, 0, 0, 0], "負": [0, 0, 0, 0]}
    for gid, s in sorted(idx.items()):
        if "unspent" not in s or s.get("my_turns", 0) < 3:
            continue
        k = "勝" if s["result"].startswith(("勝", "相手が投了")) else "負"
        agg[k][0] += 1; agg[k][1] += s["unspent"]; agg[k][2] += s["avail"]; agg[k][3] += s["hero_power"]
        print(f"{gid}  {k}   {s['my'][:14]:<14} vs {s['opp'][:14]:<14}  {s['unspent']:>3}/{s['avail']:<3} ({s['unspent']/max(1,s['avail']):.0%})  "
              f"{s['hero_power']:>2}回   " + (f"{s['coin_turn']}回目" if s['coin_turn'] else "-"))
    print()
    for k, (n, u, a, hp) in agg.items():
        if n:
            print(f"{k}: {n}試合  余りマナ率 {u/max(1,a):.1%}  ヒーローパワー平均 {hp/n:.1f}回/試合")


def load_index():
    p = GAMES_DIR / "index.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def cmd_list(_args):
    idx = load_index()
    for n, (gid, s) in enumerate(sorted(idx.items(), reverse=True), 1):
        print(f"{n:>3}  {gid}  {s['result']:<8} {s['my']} vs {s['opp']}  {s['turns']}T  {'先攻' if s['first'] else '後攻'}")


def cmd_show(args):
    idx = load_index()
    ids = sorted(idx, reverse=True)
    key = args.game
    if key == "latest":
        gid = ids[0]
    elif key.isdigit() and int(key) <= len(ids):
        gid = ids[int(key) - 1]
    else:
        gid = key
    print((GAMES_DIR / f"{gid}.md").read_text(encoding="utf-8"))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--logs", default=os.environ.get("HS_LOGS", str(DEFAULT_LOGS)))
    s.add_argument("--gametype", default="GT_RANKED")
    s.add_argument("--format", default="FT_STANDARD")
    s.add_argument("--me", help="自分のバトルタグ(通常は自動判定)")
    s.add_argument("--include-incomplete", action="store_true")
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    rb = sub.add_parser("rebuild")
    rb.add_argument("--me")
    rb.set_defaults(fn=cmd_rebuild)
    sh = sub.add_parser("show")
    sh.add_argument("game", nargs="?", default="latest")
    sh.set_defaults(fn=cmd_show)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
