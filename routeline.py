"""行进演出的承接台词与场次换线分析。

数据只来自 data/corpus.json 的合成记录。模块按记录原文取用信息，不补写
任何剧中事实：一句台词能不能对某组说，取决于该组场记里实际听过什么、
上游场景真正揭示了什么，以及换线时哪些承诺已经对观众说出口。

命令行：

    python3 -I routeline.py report
    python3 -I routeline.py plan --group 甲组 --group 乙组
    python3 -I routeline.py plan --change c1
    python3 -I routeline.py plan --after-water
    python3 -I routeline.py plan --skip hill-a --skip bridge-c
    python3 -I routeline.py check --group 乙组 --lineup bridge-c
    python3 -I routeline.py show --group 甲组
    python3 -I routeline.py validate

退出码：report/plan/show 成功为 0；check 查出错接、validate 查出结构问题
为 1；输入或参数错误为 2。
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

DEFAULT_ROOT = Path(__file__).resolve().parent
RECORD_KINDS = {"scene", "log", "change"}

CN = "一-鿿"
CH = f"[{CN}]"
COLORS = "黑红白蓝黄绿金银紫青"
FLAG = f"[{COLORS}]旗"

TITLE_RE = re.compile(r"^([^|]*)\|(.*)$", re.S)
QUOTE_RE = re.compile(r"“([^”]+)”")
SPEAKER_RE = re.compile(rf"({CH}{{1,8}}?)(?:说|道)[：:]?\s*$")
GROUP_RE = re.compile(r"([甲乙丙丁戊己庚辛]组)")
PRIOR_RE = re.compile(r"前场\s*([a-z][a-z0-9-]*)")
CAST_RE = re.compile(rf"({CH}{{2,3}}?)(?:改|重新)?(?:演|饰|扮)({CH}{{1,8}}?)(?=[，,。；;]|$)")
COLOR_FLAG_RE = re.compile(FLAG)

HANDOVER_NOTE_RE = re.compile(
    r"(?:(?:交旗|交令|交印)[^。；]{0,6}(?:由|经)(?:本场)?(?:观众|游客)"
    r"|(?:由|经)?(?:本场)?(?:观众|游客)[^。；]{0,8}(?:交|递|还)了?[^。；]{0,4}(?:旗|令))"
)
SUPPRESS_RE = re.compile(r"(?:此刻|现在|此时)?(?:不提|别提|莫提|不要提|不能提|不许提|不可提|勿提)")
ATTRIBUTION_DENIAL_RE = re.compile(
    r"(?:归属|来历)[^。；]{0,6}(?:未被|没被|不被|未获|不予)[^。；]{0,4}(?:承认|认可|作数)"
)
PROVENANCE_RE = re.compile(r"(?:来自|源自|出自)")

REQUIRES_HANDOVER_RE = re.compile(r"(?:才交来|刚交来|交来的|收了你|收了你的|你交的|你方才交)")
REQUIRES_HOLDING_RE = re.compile(r"(?:你手上|你手里|你持|你仍持)")
CLAIM_PROMISE_RE = re.compile(r"我(?:曾|已|先前)?(?:许|答应|允诺|承诺)(?:过)?你([^，。；]{1,12})")
IRONY_RE = re.compile(r"(?:别把|别信|不要交|勿交|自称|冒充|冒称|骗|说谎)")
PROPOSAL_RE = re.compile(r"(?:一道走|一起走|一同走|同行|跟我|随我|进去时|我们一道|我们先)")
PROMISE_RE = re.compile(
    r"我(?:们)?[^。]{0,20}?(?:便|就|会|要|定|答应|许)[^。]{0,10}?(?:带|领|假扮|护送|收|交)"
)
CONDITION_RE = re.compile(r"(?:等|待|只要|若|要是|一旦)[^，。；]{1,14}")
DOUBT_RE = re.compile(r"(?:是谁|什么|哪|是否|还没|未现身|尚在|不明|[?？])")
ENDING_REVEAL_RE = re.compile(
    rf"(?:就是|原来是|其实是|现身的是|揭晓|结局是)(?!谁|什么|哪|哪一个)(?:{CH}{{1,6}})"
)
AUDIENCE_COMMAND_RE = re.compile(r"(?:请你|请你们|你们|务必)[^。；]{0,8}(?:交给|递给|交到|还给我|跟我走|随我来)")

LOG_HANDOVER_RE = re.compile(rf"观众[^，。；]{{0,6}}(?:交|递|还)了?(?:一面)?({FLAG})?")
LOG_HOLDS_RE = re.compile(rf"(?:仍|还)?持(?:有)?({FLAG})")
LOG_PROMISE_RE = re.compile(r"(?:确已|已经|已)?(?:许诺|答应|允诺|承诺)([^，。；]{1,16})")
LOG_WARNING_RE = re.compile(r"(?:只)?(?:听到|听见|闻知)([^，。；]{1,20})")
LOG_KNOWS_RE = re.compile(r"(?:已经|已|确已)(?:看清|认出|确认|知道)[^。；]{0,12}(?:是谁|身份|对岸)")
LOG_KNOWS_NEG_RE = re.compile(r"(?:未|没|没有|不)(?:看清|认出|确认|知道)")
LOG_DENIAL_RE = re.compile(r"(?:未被|没被|不被|不予)[^。；]{0,6}(?:承认|认可|作数)")

GROUP_JOIN_RE = re.compile(r"([甲乙丙丁戊己庚辛])([甲乙丙丁戊己庚辛])(?=在|合)")
SUSPEND_RE = re.compile(r"(山腰|水边|桥口|山顶)[^，。；]{0,8}(?:暂停|停演|不开|封闭)")
KEEP_PROMISE_RE = re.compile(r"保留[^，。；]{0,10}(?:许诺|承诺|说出口)")

STANCE_LABEL = {
    "promise": "角色应允",
    "proposal": "提议",
    "irony": "反话/告诫",
    "claim": "反话（冒认旧诺）",
    "hint": "提示",
}


class CorpusError(ValueError):
    """语料或参数不合法；命令入口转成退出码 2。"""


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class Quote:
    text: str
    speaker: Optional[str]
    stance: str
    flag: Optional[str]
    doubt: bool
    condition: Optional[str]
    claim_phrase: Optional[str]


@dataclass(frozen=True)
class Note:
    text: str
    kind: str  # handover | suppression | attribution | provenance | note
    flag: Optional[str]


@dataclass(frozen=True)
class Scene:
    id: str
    title: str
    body: str
    order: int
    quotes: Tuple[Quote, ...]
    notes: Tuple[Note, ...]
    flags: frozenset
    fills_handover: bool
    forbids_flag_talk: bool
    denies_attribution: bool
    requires_handover: bool
    requires_holding: bool
    doubt: bool
    claim_phrase: Optional[str]
    upstream_hint: Tuple[str, ...]


@dataclass(frozen=True)
class Change:
    id: str
    joined: frozenset
    keeps_spoken_promise: bool
    suspended: frozenset
    text: str


@dataclass
class GroupState:
    group: str
    cast: Dict[str, str] = field(default_factory=dict)
    cast_source: Dict[str, str] = field(default_factory=dict)
    performed: List[str] = field(default_factory=list)
    handovers: Set[str] = field(default_factory=set)
    handover_logs: List[str] = field(default_factory=list)
    holdings: Set[str] = field(default_factory=set)
    holding_logs: List[str] = field(default_factory=list)
    promises: Dict[str, str] = field(default_factory=dict)
    promise_logs: List[str] = field(default_factory=list)
    heard: List[Tuple[str, str, str]] = field(default_factory=list)
    heard_notes: List[str] = field(default_factory=list)
    knows_answer: bool = False
    claims: List[str] = field(default_factory=list)
    conflicts: Dict[str, Set[str]] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)


@dataclass
class Corpus:
    name: str
    scenes: Dict[str, Scene]
    states: Dict[str, GroupState]
    changes: Dict[str, Change]
    violations: Tuple[str, ...] = ()


@dataclass
class Evaluation:
    scene_id: str
    ok: bool
    stance: str
    reasons: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    implied: List[Dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------- 读取


def load(path: Optional[Path] = None) -> Corpus:
    path = Path(path) if path else DEFAULT_ROOT / "data" / "corpus.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError(f"找不到语料文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"语料不是合法 JSON：{path}: {exc}") from exc
    return parse(data)


def parse(data: Mapping) -> Corpus:
    if not isinstance(data, Mapping) or not isinstance(data.get("records"), list):
        raise CorpusError("语料缺少 records 列表")
    records = data["records"]
    if any(not isinstance(r, Mapping) for r in records):
        raise CorpusError("records 中存在非对象条目")
    ids = [r.get("id") for r in records]
    if len(ids) != len(set(ids)):
        raise CorpusError("记录 id 重复")
    for record in records:
        if record.get("kind") not in RECORD_KINDS:
            raise CorpusError(f"未知的记录类别：{record.get('kind')!r}")
        if not isinstance(record.get("text"), str) or not record["text"].strip():
            raise CorpusError(f"记录 {record.get('id')} 缺少原文")

    scenes = {r["id"]: _parse_scene(r, i) for i, r in enumerate(records) if r["kind"] == "scene"}
    states = _build_states([r for r in records if r["kind"] == "log"], scenes)
    changes = {r["id"]: _parse_change(r, scenes) for r in records if r["kind"] == "change"}
    corpus = Corpus(name=str(data.get("project", "")), scenes=scenes, states=states, changes=changes)
    corpus.violations = tuple(_validate(corpus))
    return corpus


def _parse_scene(record: Mapping, order: int) -> Scene:
    text = record["text"].strip()
    title_match = TITLE_RE.match(text)
    title, body = (title_match.group(1).strip(), title_match.group(2).strip()) if title_match else (text, "")
    quotes: List[Quote] = []
    notes: List[Note] = []
    actor_tags: List[str] = []
    cursor = 0
    for quote_match in QUOTE_RE.finditer(body):
        segment = body[cursor:quote_match.start()]
        speaker, segment = _speaker_before(segment)
        if speaker:
            actor_tags.append(speaker)
        notes.extend(_parse_notes(segment))
        notes = [n for n in notes if not SPEAKER_ONLY_RE.fullmatch(n.text)]
        quotes.append(_parse_quote(quote_match.group(1), speaker))
        cursor = quote_match.end()
    notes.extend(_parse_notes(body[cursor:]))
    notes = [n for n in notes if not SPEAKER_ONLY_RE.fullmatch(n.text)]

    flags: Set[str] = set()
    for quote in quotes:
        if quote.flag:
            flags.add(quote.flag)
    for note in notes:
        if note.flag:
            flags.add(note.flag)
    has_handover = any(n.kind == "handover" for n in notes)
    return Scene(
        id=record["id"],
        title=title,
        body=body,
        order=order,
        quotes=tuple(quotes),
        notes=tuple(notes),
        flags=frozenset(flags),
        fills_handover=has_handover,
        forbids_flag_talk=any(n.kind == "suppression" for n in notes),
        denies_attribution=any(n.kind == "attribution" for n in notes),
        requires_handover=has_handover or any(REQUIRES_HANDOVER_RE.search(q.text) for q in quotes),
        requires_holding=any(REQUIRES_HOLDING_RE.search(q.text) for q in quotes),
        doubt=any(q.doubt for q in quotes),
        claim_phrase=next((q.claim_phrase for q in quotes if q.claim_phrase), None),
        upstream_hint=tuple(PRIOR_RE.findall(text)),
    )


def _parse_notes(text: str) -> List[Note]:
    notes: List[Note] = []
    if "|" in text:
        text = text.split("|", 1)[0]
    for sentence in re.split(r"[。；;！？\n]", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        flag = _first_flag(sentence)
        if HANDOVER_NOTE_RE.search(sentence):
            notes.append(Note(sentence, "handover", flag))
        elif SUPPRESS_RE.search(sentence):
            notes.append(Note(sentence, "suppression", flag))
        elif ATTRIBUTION_DENIAL_RE.search(sentence):
            notes.append(Note(sentence, "attribution", flag))
        elif PROVENANCE_RE.search(sentence):
            notes.append(Note(sentence, "provenance", flag))
        else:
            notes.append(Note(sentence, "note", flag))
    return notes


def _first_flag(text: str) -> Optional[str]:
    match = COLOR_FLAG_RE.search(text)
    return match.group(0) if match else None


SPEAKER_ONLY_RE = re.compile(rf"{CH}{{1,8}}?(?:说|道)[：:]?")


def _speaker_before(text: str) -> Tuple[Optional[str], str]:
    """拆出“甲说：”这一截，返回（角色，剩余动作叙述）。"""
    tail = text.split("|")[-1]
    for sentence in reversed(re.split(r"[。！？；;\n]", tail)):
        match = SPEAKER_RE.search(sentence)
        if not match:
            continue
        return match.group(1), tail.replace(sentence, "", 1)
    return None, text.split("|")[-1]


def _parse_quote(text: str, speaker: Optional[str]) -> Quote:
    claim = CLAIM_PROMISE_RE.search(text)
    if IRONY_RE.search(text):
        stance = "irony"
    elif claim:
        stance = "claim"
    elif PROPOSAL_RE.search(text):
        stance = "proposal"
    elif PROMISE_RE.search(text) and not DOUBT_RE.search(text):
        stance = "promise"
    else:
        stance = "hint"
    condition = CONDITION_RE.search(text)
    return Quote(
        text=text,
        speaker=speaker,
        stance=stance,
        flag=_first_flag(text),
        doubt=bool(DOUBT_RE.search(text)),
        condition=condition.group(0) if condition else None,
        claim_phrase=claim.group(1).strip() if claim else None,
    )


def _parse_change(record: Mapping, scenes: Mapping[str, Scene]) -> Change:
    text = record["text"]
    joined: Set[str] = set()
    for left, right in GROUP_JOIN_RE.findall(text):
        joined.update({left + "组", right + "组"})
    if "合场" in text:
        joined.update(GROUP_RE.findall(text))
    suspended: Set[str] = set()
    for place in SUSPEND_RE.findall(text):
        for scene in scenes.values():
            if scene.title.startswith(place):
                suspended.add(scene.id)
    return Change(
        id=record["id"],
        joined=frozenset(joined),
        keeps_spoken_promise=bool(KEEP_PROMISE_RE.search(text)),
        suspended=frozenset(suspended),
        text=text,
    )


def _build_states(log_records: Sequence[Mapping], scenes: Mapping[str, Scene]) -> Dict[str, GroupState]:
    states: Dict[str, GroupState] = {}
    for record in log_records:
        text = record["text"]
        groups = GROUP_RE.findall(text) or ["未标注组"]
        prior = PRIOR_RE.findall(text)
        for group in groups:
            state = states.setdefault(group, GroupState(group=group))
            state.logs.append(record["id"])
            for scene_id in prior:
                if scene_id not in state.performed:
                    state.performed.append(scene_id)
            for name, role in CAST_RE.findall(text):
                role = role.strip()
                if role in state.cast and state.cast[role] != name:
                    state.conflicts.setdefault(role, {state.cast[role]}).add(name)
                state.cast.setdefault(role, name)
                state.cast_source.setdefault(role, record["id"])

            handover = LOG_HANDOVER_RE.search(text)
            if handover and not re.search(r"(?:未|没|没有|不曾)[^，。；]{0,4}(?:交|递|还)", text):
                flag = handover.group(1) or _scene_handover_flag(scenes, prior)
                if flag:
                    state.handovers.add(flag)
                state.handover_logs.append(record["id"])

            for flag in LOG_HOLDS_RE.findall(text):
                state.holdings.add(flag)
                state.holding_logs.append(record["id"])

            promise = LOG_PROMISE_RE.search(text)
            if promise:
                state.promises.setdefault(promise.group(1).strip(), record["id"])
                state.promise_logs.append(record["id"])

            warning = LOG_WARNING_RE.search(text)
            if warning:
                state.heard_notes.append(warning.group(1).strip())

            if LOG_KNOWS_RE.search(text) and not LOG_KNOWS_NEG_RE.search(text):
                state.knows_answer = True

            if LOG_DENIAL_RE.search(text) and "归属未被承认" not in state.claims:
                state.claims.append("归属未被承认")

            for scene_id in prior:
                scene = scenes.get(scene_id)
                if scene:
                    for quote in scene.quotes:
                        state.heard.append((scene.id, quote.text, record["id"]))
    return states


def _scene_handover_flag(scenes: Mapping[str, Scene], upstream: Iterable[str]) -> Optional[str]:
    for scene_id in upstream:
        scene = scenes.get(scene_id)
        if scene and scene.fills_handover:
            for note in scene.notes:
                if note.kind == "handover" and note.flag:
                    return note.flag
            for quote in scene.quotes:
                if quote.flag:
                    return quote.flag
    return None


# ---------------------------------------------------------------- 身份


def _role_match(left: str, right: str) -> bool:
    """按场次与令牌适用范围认身份：允许“商队/商队掌柜”这类同场别名，不跨关系。"""
    if left == right:
        return True
    return len(left) >= 2 and len(right) >= 2 and (left.startswith(right) or right.startswith(left))


def find_cast(state: GroupState, role: str) -> Optional[Tuple[str, str]]:
    for known, actor in state.cast.items():
        if _role_match(known, role):
            return known, actor
    return None


def _global_role_actors(corpus: Corpus, role: str) -> List[Tuple[str, str]]:
    hits = []
    for group, state in corpus.states.items():
        found = find_cast(state, role)
        if found:
            hits.append((group, found[1]))
    return hits


def identity_reason(corpus: Corpus, group: str, scene: Scene) -> List[str]:
    """这场戏的每个说话角色都必须在该组自己的场次里登记过。"""
    state = corpus.states[group]
    reasons: List[str] = []
    for quote in scene.quotes:
        if not quote.speaker:
            continue
        found = find_cast(state, quote.speaker)
        if found:
            continue
        role_note = "（『商队』按本场令牌可视为『商队掌柜』）" if quote.speaker == "商队" else ""
        reasons.append(f"本组班底没有会演『{quote.speaker}』的演员{role_note}，这句不能对{group}说")
        for other_group, actor in _global_role_actors(corpus, quote.speaker):
            if actor in state.cast.values():
                own_role = next(r for r, a in state.cast.items() if a == actor)
                reasons.append(
                    f"同一演员{actor}在{group}登记为『{own_role}』，在{other_group}登记为"
                    f"『{quote.speaker}』；按场次认身份，不能把另一场的关系串过来"
                )
    return reasons


# ---------------------------------------------------------------- 依赖与判定


def promise_matches(state: GroupState, phrase: str) -> List[Tuple[str, str]]:
    return [
        (spoken, log) for spoken, log in state.promises.items()
        if spoken == phrase or spoken in phrase or phrase in spoken
    ]


def dependency_providers(corpus: Corpus, scene: Scene) -> Dict[str, List[str]]:
    """从剧本文字推断这幕依赖哪些上游事实，以及语料里谁能提供。"""
    deps: Dict[str, List[str]] = {}
    providers = [s.id for s in _ordered(corpus) if s.fills_handover]
    if scene.requires_handover and not scene.fills_handover:
        deps["现场交旗"] = providers or ["无（语料里没有交旗的幕）"]
    elif scene.fills_handover:
        deps["本幕完成交旗"] = [scene.id]
    if scene.claim_phrase:
        deps[f"许诺“{scene.claim_phrase}”"] = [
            s.id for s in _ordered(corpus)
            if s.id != scene.id and (
                any(q.claim_phrase and (q.claim_phrase in scene.claim_phrase
                                        or scene.claim_phrase in q.claim_phrase) for q in s.quotes)
                or any(q.stance == "promise" for q in s.quotes)
            )
        ] or ["无（场记里无人许过）"]
    if scene.requires_holding:
        deps["观众持旗"] = [
            s.id for s in _ordered(corpus) if any(n.kind == "provenance" for n in s.notes)
        ] or ["无（场记里没有持旗来源）"]
    return deps


def _upstream_owners(corpus: Corpus, group: str, dependency: str) -> List[str]:
    owners = []
    for other, state in corpus.states.items():
        if other == group:
            continue
        if dependency == "现场交旗" and state.handovers:
            owners.append(other)
        elif dependency == "观众持旗" and state.holdings:
            owners.append(other)
        elif dependency.startswith("许诺") and state.promises:
            owners.append(other)
    return owners


def _promise_holders(corpus: Corpus, phrase: str) -> List[str]:
    """这条旧诺实际说给过哪些组；用于防止把别组的旧诺冒认成本组的。"""
    return [
        group for group, state in corpus.states.items()
        if any(p == phrase or p in phrase or phrase in p for p in state.promises)
    ]


def reveals_ending(scene: Scene) -> bool:
    corpus_text = scene.body + " ".join(q.text for q in scene.quotes)
    return bool(ENDING_REVEAL_RE.search(corpus_text))


def unsafe_rewrite(scene: Scene) -> List[str]:
    """跳过约束的改写不许乱发令，也不许提前公布结局。"""
    issues: List[str] = []
    if reveals_ending(scene):
        issues.append("提前公布结局：写明了对岸来人是谁")
    notes_text = "；".join(n.text for n in scene.notes)
    if AUDIENCE_COMMAND_RE.search(notes_text) and not scene.fills_handover:
        issues.append("乱发令：要求观众交旗或跟随，但本幕没有对应的现场条件")
    return issues


def evaluate(corpus: Corpus, group: str, scene: Scene) -> Evaluation:
    state = corpus.states.get(group)
    if state is None:
        raise CorpusError(f"场记里没有 {group} 的任何记录")
    stance = STANCE_LABEL["hint"]
    if scene.quotes:
        styles = [q.stance for q in scene.quotes]
        if "claim" in styles:
            stance = STANCE_LABEL["claim"]
        elif "irony" in styles:
            stance = STANCE_LABEL["irony"]
        else:
            stance = STANCE_LABEL[styles[0]]
    result = Evaluation(scene_id=scene.id, ok=True, stance=stance)

    # 1. 身份：按场次和令牌适用范围认人。
    result.reasons.extend(identity_reason(corpus, group, scene))
    for quote in scene.quotes:
        if quote.speaker:
            found = find_cast(state, quote.speaker)
            if found:
                result.sources.append(
                    f"身份：{state.cast_source.get(found[0], '?')} 登记 "
                    f"{found[1]}演『{found[0]}』"
                )

    # 2. 省掉的持旗人：必须有该组自己的现场交旗记录。
    if scene.requires_handover:
        if state.handovers:
            logs = "、".join(dict.fromkeys(state.handover_logs))
            result.sources.append(
                f"支撑：本组已在现场交旗（{'、'.join(sorted(state.handovers))}；场记 {logs}）"
            )
            result.implied.append({
                "省掉的成分": "交旗人（你）",
                "指认": f"{group}（刚交旗的观众）",
                "依据": "台词“才交来的那面旗”＋场记交旗记录",
            })
        else:
            result.reasons.append(
                "台词省掉交旗人，只对“刚交来旗”的本场观众成立；本组场记没有交旗记录"
            )
            owners = _upstream_owners(corpus, group, "现场交旗")
            providers = [s.id for s in _ordered(corpus) if s.fills_handover]
            if owners:
                result.reasons.append(
                    f"交旗关系属于{'、'.join(owners)}（上游 {'、'.join(providers)}），不能借给{group}"
                )

    # 3. 省掉的持旗人：手上必须仍有旗。
    if scene.requires_holding:
        if state.holdings:
            result.implied.append({
                "省掉的成分": "持旗人（你）",
                "指认": f"{group}（仍持{'、'.join(sorted(state.holdings))}）",
                "依据": "台词“你手上的旗”＋场记持旗记录",
            })
            result.sources.append("道具：本组仍持" + "、".join(sorted(state.holdings)))
        else:
            result.reasons.append("台词以“你手上的旗”指认持旗人，但本组场记没有持旗记录")
            owners = _upstream_owners(corpus, group, "观众持旗")
            if owners:
                result.reasons.append(f"持旗的是{'、'.join(owners)}，旗已交出或不在本组手上")

    # 4. 台词自称的旧诺，必须对得上该组实际听过的许诺。
    if scene.claim_phrase:
        matched = promise_matches(state, scene.claim_phrase)
        if matched:
            for spoken, log in matched:
                result.sources.append(f"支撑：场记 {log} 记录已许诺“{spoken}”")
        else:
            spoken = "、".join(f"“{p}”" for p in state.promises) or "没有许诺"
            result.reasons.append(
                f"台词称“我许你{scene.claim_phrase}”，本组场记里的许诺却是{spoken}，不是同一承诺"
            )
            holders = _promise_holders(corpus, scene.claim_phrase)
            if holders and holders != [group]:
                result.reasons.append(f"旧诺实际说给{'、'.join(holders)}，不能改口成本组的承诺")
            elif not state.promises:
                result.reasons.append("本组没有可对应的旧诺记录，只能按反话处理")

    # 5. 本幕注明归属未被承认时，不能拿来自称旧物。
    if scene.denies_attribution and (state.holdings or state.claims):
        result.reasons.append(
            "本幕注明该旗归属未被承认，台词里的“旧物/还来”不能作为本组的承诺依据"
        )

    # 5b. 提议与应允要分开：没有明确承诺的同行邀请不能当成已许带过桥。
    proposal_only = any(q.stance == "proposal" for q in scene.quotes) and not any(
        q.stance == "promise" for q in scene.quotes
    )
    if proposal_only and state.promises:
        result.sources.append("语气：这是提议，不另立承诺；本组已说出口的承诺仍按原记录保留")

    # 6. 引用疑问的台词，只能给仍留有疑问的版本。
    if scene.doubt:
        if state.knows_answer:
            result.reasons.append(
                "台词引用“还没现身的是谁”这类猜测，本组场记已看清答案，不能再用留疑版本"
            )
        else:
            result.sources.append("版本：疑问仍未揭晓，可按原句使用")

    result.reasons = list(dict.fromkeys(result.reasons))
    result.sources = list(dict.fromkeys(result.sources))
    result.ok = not result.reasons and not unsafe_rewrite(scene)
    return result


def classify(corpus: Corpus, scene: Scene) -> str:
    """shared：不依赖上游的共用提示；dependent：依赖上游；unsafe：不能直接用。"""
    if unsafe_rewrite(scene):
        return "unsafe"
    if scene.requires_handover or scene.requires_holding or scene.claim_phrase:
        return "dependent"
    return "shared"


def _ordered(corpus: Corpus) -> List[Scene]:
    return sorted(corpus.scenes.values(), key=lambda s: s.order)


# ---------------------------------------------------------------- 场次安排


def _leaks(scene: Scene, corpus: Corpus, group: str) -> List[str]:
    """仅给一组的承接提示，不得透露另一组的身份和剧情。"""
    leaks: List[str] = []
    text = scene.body + " ".join(q.text for q in scene.quotes)
    for other, state in corpus.states.items():
        if other == group:
            continue
        for role in state.cast:
            if role and role in text:
                leaks.append(f"提到{other}的角色『{role}』")
        for actor in state.cast.values():
            if actor and actor in text:
                leaks.append(f"提到{other}的演员{actor}")
        for flag in state.holdings | state.handovers:
            if flag and flag in text:
                leaks.append(f"提到{other}的{flag}")
        for scene_id, quote, _ in state.heard:
            if quote and quote[:6] in text:
                leaks.append(f"复述{other}听过的台词（{scene_id}）")
    return list(dict.fromkeys(leaks))


def _shared_hint(corpus: Corpus, groups: Sequence[str], skip: Set[str]) -> Optional[Scene]:
    for scene in _ordered(corpus):
        if scene.id in skip or classify(corpus, scene) != "shared":
            continue
        if all(evaluate(corpus, group, scene).ok for group in groups):
            return scene
    return None


def _playable(corpus: Corpus, group: str, skip: Set[str]) -> Tuple[List[Scene], List[Tuple[Scene, Evaluation]]]:
    state = corpus.states[group]
    playable, blocked = [], []
    for scene in _ordered(corpus):
        if scene.id in skip or scene.id in state.performed:
            continue
        verdict = evaluate(corpus, group, scene)
        if verdict.ok:
            playable.append(scene)
        else:
            blocked.append((scene, verdict))
    return playable, blocked


def _fulfillers(corpus: Corpus, group: str, skip: Set[str]) -> List[Scene]:
    state = corpus.states[group]
    if not state.promises:
        return []
    out = []
    for scene in _ordered(corpus):
        if scene.id in skip or scene.id in state.performed:
            continue
        if classify(corpus, scene) != "dependent":
            continue
        if any(q.stance in {"proposal", "promise"} for q in scene.quotes):
            if evaluate(corpus, group, scene).ok:
                out.append(scene)
    return out


def _stranded_reasons(corpus: Corpus, group: str, skip: Set[str]) -> List[Dict]:
    state = corpus.states[group]
    reasons = []
    for scene in _ordered(corpus):
        if scene.id in state.performed:
            continue
        if scene.id in skip:
            reasons.append({"幕": scene.id, "原因": "换线时被跳过，未演"})
        elif classify(corpus, scene) == "dependent":
            reasons.append({"幕": scene.id, "原因": evaluate(corpus, group, scene).reasons})
    if not reasons:
        reasons.append({"幕": None, "原因": "语料里没有能履行该承诺的衔接幕"})
    return reasons


def _scene_entry(corpus: Corpus, group: str, scene: Scene) -> Dict:
    verdict = evaluate(corpus, group, scene)
    return {
        "幕": scene.id,
        "标题": scene.title,
        "分类": classify(corpus, scene),
        "台词": [q.text for q in scene.quotes],
        "语气": verdict.stance,
        "支撑": verdict.sources,
        "隐含指认": verdict.implied,
        "限制": "此刻不提旗，只作开场" if scene.forbids_flag_talk else None,
    }


def plan(corpus: Corpus, groups: Sequence[str], skip: Iterable[str] = (), join: bool = False) -> Dict:
    groups = tuple(groups)
    if not groups:
        raise CorpusError("至少要给一组观众安排场次")
    for group in groups:
        if group not in corpus.states:
            raise CorpusError(f"场记里没有 {group} 的任何记录")
    skip_set = set(dict.fromkeys(skip))
    for scene_id in skip_set:
        if scene_id not in corpus.scenes:
            raise CorpusError(f"跳过列表里有不存在的幕：{scene_id}")
    if join and len(groups) < 2:
        raise CorpusError("合场至少需要两组观众")

    shared = _shared_hint(corpus, groups, skip_set) if len(groups) > 1 else None
    per_group: Dict[str, Dict] = {}
    for group in groups:
        state = corpus.states[group]
        playable, blocked = _playable(corpus, group, skip_set)
        per_group[group] = {
            "可继续": [_scene_entry(corpus, group, s) for s in playable],
            "已演": list(state.performed),
            "不能演": [
                {"幕": s.id, "原因": v.reasons}
                for s, v in blocked
            ],
            "保留的已出口承诺": [
                {"承诺": phrase, "场记": log, "处理": "换线时保留，不与未演安排一起清空"}
                for phrase, log in state.promises.items()
            ],
        }

    joined: Dict = {"可合": False, "依据": [], "分组补演": []}
    if len(groups) > 1:
        joined["可合"] = shared is not None
        joined["依据"].append(
            f"共用提示 {shared.id}（{shared.title}）两组都能听懂"
            + ("，且只能先作开场、不提旗" if shared.forbids_flag_talk else "")
            if shared else "没有两组都能听懂、且不依赖各自上游的共用提示"
        )
        for group in groups:
            playable, blocked = _playable(corpus, group, skip_set)
            private = next((s for s in playable if classify(corpus, s) == "dependent"), None)
            if private:
                leaks = _leaks(private, corpus, group)
                entry = {
                    "组": group,
                    "幕": private.id,
                    "台词": [q.text for q in private.quotes],
                    "不透露": {
                        "其他组": [g for g in groups if g != group],
                        "身份": "其他组的演员与角色对应",
                        "剧情": "其他组听过的告诫与许诺",
                    },
                }
                if leaks:
                    entry["必须改写"] = leaks
                joined["单组承接"] = joined.get("单组承接", []) + [entry]
            for scene, verdict in blocked:
                if shared and scene.id == shared.id:
                    continue
                joined["分组补演"].append({
                    "组": group,
                    "幕": scene.id,
                    "原因": verdict.reasons,
                })
        for actor, roles in _actor_roster(corpus, groups).items():
            joined["依据"].append(f"{actor} 在这一线上分别扮演 " + "、".join(roles)
                                  + "；合场时按场次认身份，关系不得互相借用")
        if not any(entry["组"] for entry in joined["分组补演"]):
            joined["分组补演"].append({"组": None, "原因": "两组都能接上共用提示，无需补演"})

    stranded = []
    for group in groups:
        state = corpus.states[group]
        if not state.promises or _fulfillers(corpus, group, skip_set):
            continue
        playable, _ = _playable(corpus, group, skip_set)
        stranded.append({
            "组": group,
            "未履行的承诺": dict(state.promises),
            "可以先演": [s.id for s in playable],
            "必须分组补演": _stranded_reasons(corpus, group, skip_set),
            "原因": "换线后已没有能履行该承诺的同幕衔接方式",
        })

    versions = []
    for scene in _ordered(corpus):
        if scene.id in skip_set:
            continue
        deps = dependency_providers(corpus, scene)
        versions.append({
            "幕": scene.id,
            "标题": scene.title,
            "分类": classify(corpus, scene),
            "依赖": _dependency_display(corpus, scene),
            "各组合用": {g: evaluate(corpus, g, scene).ok for g in groups},
            "禁止改写": unsafe_rewrite(scene),
        })

    return {
        "线路": list(groups),
        "跳过的幕": sorted(skip_set),
        "共用提示": {
            "幕": shared.id,
            "标题": shared.title,
            "台词": [q.text for q in shared.quotes],
            "限制": "此刻不提旗，只用于开场" if shared.forbids_flag_talk else None,
        } if shared else None,
        "分组安排": per_group,
        "合场": joined,
        "保留的已出口承诺": {
            g: dict(corpus.states[g].promises) for g in groups if corpus.states[g].promises
        },
        "未履行承诺": stranded,
        "衔接版本": versions,
    }


def _actor_roster(corpus: Corpus, groups: Sequence[str]) -> Dict[str, List[str]]:
    roster: Dict[str, List[str]] = {}
    for group in groups:
        for role, actor in corpus.states[group].cast.items():
            roster.setdefault(actor, []).append(f"{group}的『{role}』")
    return {actor: roles for actor, roles in roster.items() if len(roles) > 1}


def check_lineup(corpus: Corpus, group: str, lineup: Sequence[str]) -> Dict:
    if group not in corpus.states:
        raise CorpusError(f"场记里没有 {group} 的任何记录")
    errors, warnings = [], []
    previous: Optional[Scene] = None
    for scene_id in lineup:
        scene = corpus.scenes.get(scene_id)
        if scene is None:
            errors.append({"幕": scene_id, "问题": "语料里没有这幕"})
            continue
        verdict = evaluate(corpus, group, scene)
        if not verdict.ok:
            errors.append({
                "幕": scene_id,
                "问题": "错接",
                "原因": verdict.reasons + unsafe_rewrite(scene),
                "台词": [q.text for q in scene.quotes],
            })
        if previous and previous.forbids_flag_talk and (scene.requires_holding or scene.requires_handover):
            warnings.append({
                "幕": scene_id,
                "提醒": f"前一幕 {previous.id} 写明此刻不提旗，紧接这一幕会立刻提旗",
            })
        previous = scene
    return {"组": group, "线": list(lineup), "错接": errors, "提醒": warnings, "通过": not errors}


def _dependency_display(corpus: Corpus, scene: Scene) -> Dict[str, str]:
    deps = dependency_providers(corpus, scene)
    display = {}
    for name, providers in deps.items():
        if name.startswith("许诺"):
            holders = _promise_holders(corpus, scene.claim_phrase or "")
            display[name] = (
                "语料里无人许过这条旧诺" if not holders
                else "场记里由" + "、".join(holders) + "实际听到过近似许诺"
            )
        else:
            display[name] = "、".join(providers) if providers else "语料里没有提供者"
    return display or {"依赖": "无（不依赖上游承诺）"}


def _validate(corpus: Corpus) -> Iterable[str]:
    for scene in corpus.scenes.values():
        for upstream in scene.upstream_hint:
            if upstream not in corpus.scenes:
                yield f"{scene.id} 引用不存在的上游幕 {upstream}"
    for group, state in corpus.states.items():
        for role, names in state.conflicts.items():
            yield f"{group} 的角色『{role}』由不同演员出现：{'、'.join(sorted(names))}"
        for scene_id in state.performed:
            if scene_id not in corpus.scenes:
                yield f"{group} 的前场记录引用了不存在的幕 {scene_id}"


# ---------------------------------------------------------------- 报告与命令


def report(corpus: Corpus) -> Dict:
    return {
        "项目": corpus.name,
        "幕": {
            scene.id: {
                "标题": scene.title,
                "分类": classify(corpus, scene),
                "台词": [
                    {
                        "说话人": q.speaker or "未指派",
                        "语气": STANCE_LABEL[q.stance],
                        "文本": q.text,
                        "道具": q.flag,
                        "疑问": q.doubt,
                        "条件": q.condition,
                    }
                    for q in scene.quotes
                ],
                "动作与说明": [{"类型": n.kind, "文本": n.text, "道具": n.flag} for n in scene.notes],
                "依赖": _dependency_display(corpus, scene),
                "禁止改写": unsafe_rewrite(scene),
            }
            for scene in _ordered(corpus)
        },
        "场记": {
            group: {
                "班底": state.cast,
                "已演": list(state.performed),
                "交旗": sorted(state.handovers),
                "持旗": sorted(state.holdings),
                "许诺": dict(state.promises),
                "听过": [{"幕": sid, "台词": quote, "场记": log}
                        for sid, quote, log in state.heard],
                "场记记下的听感": list(state.heard_notes),
                "声明": list(state.claims),
                "场记编号": list(state.logs),
            }
            for group, state in corpus.states.items()
        },
        "换线": {
            change.id: {
                "合场": sorted(change.joined),
                "暂停": sorted(change.suspended),
                "保留已出口承诺": change.keeps_spoken_promise,
                "原文": change.text,
            }
            for change in corpus.changes.values()
        },
        "检查": list(corpus.violations),
    }


def show(corpus: Corpus, group: str) -> Dict:
    if group not in corpus.states:
        raise CorpusError(f"场记里没有 {group} 的任何记录")
    state = corpus.states[group]
    return {
        "组": group,
        "实际听过的台词": [{"幕": sid, "台词": quote, "场记": log}
                          for sid, quote, log in state.heard],
        "场记记下的听感": list(state.heard_notes),
        "班底与来源": {role: {"演员": actor, "场记": state.cast_source.get(role)}
                    for role, actor in state.cast.items()},
        "完成与持有": {
            "交旗": sorted(state.handovers),
            "持旗": sorted(state.holdings),
            "许诺": dict(state.promises),
        },
        "声明": list(state.claims),
        "场记编号": list(state.logs),
    }


def _emit(payload: Mapping) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))


def _json_default(value):
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"无法序列化：{type(value)!r}")


def _split_lineup(values: Sequence[str]) -> List[str]:
    lineup: List[str] = []
    for value in values:
        lineup.extend(part for part in re.split(r"[,，\s]+", value) if part)
    return lineup


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="行进演出的承接台词与场次换线分析")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="项目根目录（含 data/corpus.json）")
    parser.add_argument("--corpus", type=Path, help="直接指定语料文件，优先于 --root")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("report", help="列出幕、场记与分类")
    plan_parser = sub.add_parser("plan", help="安排可继续的场次")
    plan_parser.add_argument("--group", action="append", default=[], help="可重复，默认甲乙两组")
    plan_parser.add_argument("--join", action="store_true", help="按合场判断")
    plan_parser.add_argument("--skip", action="append", default=[], help="跳过的幕，可重复")
    plan_parser.add_argument("--change", help="应用一条换线公告")
    plan_parser.add_argument("--after-water", action="store_true", help="按水路引子后的场次重排")
    check_parser = sub.add_parser("check", help="排演前检查一条线上的错接")
    check_parser.add_argument("--group", required=True)
    check_parser.add_argument("--lineup", action="append", required=True, help="可重复，也接受逗号分隔")
    show_parser = sub.add_parser("show", help="回查某组实际听过哪些话")
    show_parser.add_argument("--group", required=True)
    sub.add_parser("validate", help="检查语料结构")

    args = parser.parse_args(argv)
    try:
        corpus = load(args.corpus or (args.root / "data" / "corpus.json"))
    except CorpusError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    command = args.command or "report"
    if command == "validate":
        _emit({"检查": list(corpus.violations), "通过": not corpus.violations})
        return 0 if not corpus.violations else 1
    if command == "report":
        _emit(report(corpus))
        return 0
    if command == "show":
        try:
            _emit(show(corpus, args.group))
        except CorpusError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2
        return 0
    if command == "check":
        try:
            result = check_lineup(corpus, args.group, _split_lineup(args.lineup))
        except CorpusError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2
        _emit(result)
        return 0 if result["通过"] else 1

    try:
        groups = tuple(args.group) if args.group else ("甲组", "乙组")
        skip = list(args.skip)
        join = args.join or len(groups) > 1
        if args.change:
            change = corpus.changes.get(args.change)
            if change is None:
                raise CorpusError(f"没有这条换线公告：{args.change}")
            if not args.group:
                groups = tuple(sorted(change.joined))
            skip.extend(change.suspended)
            join = join or len(groups) > 1
        if args.after_water:
            skip.append("hill-a")
        result = plan(corpus, groups, skip=tuple(dict.fromkeys(skip)), join=join)
    except CorpusError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    result["检查"] = list(corpus.violations)
    _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
