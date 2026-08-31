"""Rule-based text detector: regex + dictionary heuristics, fully offline.

Structured fields (phone / id_card / email / credit_code / stock_code / ...)
use exact regexes, optionally hardened with checksum verification. Fuzzy fields
(person_name / company_name) use the heuristic patterns borrowed from
pdf-desensitizer: context labels and surname/suffix dictionaries with boundary
validation.
"""

from __future__ import annotations

import re

from pysanitize.parser.document import ParsedDocument

from .base import Detection, TextDetector
from .specs import FieldSpec, load_field_specs, select_specs

# --------------------------------------------------------------------------
# Checksums (GB 11643-1999 for ID card, GB 32100-2015 for unified credit code)
# --------------------------------------------------------------------------

_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CHECK_CHARS = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]


def _valid_id_checksum(s: str) -> bool:
    if len(s) != 18:
        return False
    total = sum(int(ch) * w for ch, w in zip(s[:17], _ID_WEIGHTS))
    return _ID_CHECK_CHARS[total % 11] == s[17].upper()


# GB 32100 alphabet excludes I, O, S, V, Z.
_USCC_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
_USCC_INDEX = {ch: i for i, ch in enumerate(_USCC_ALPHABET)}


def _valid_uscc_checksum(s: str) -> bool:
    if len(s) != 18:
        return False
    try:
        total = sum(_USCC_INDEX[ch] * w for ch, w in zip(s[:17], _USCC_WEIGHTS))
    except KeyError:
        return False
    return _USCC_ALPHABET[(31 - total % 31) % 31] == s[17]


# --------------------------------------------------------------------------
# Person-name heuristics (pdf-desensitizer style: surnames + context)
# --------------------------------------------------------------------------

# The classic Hundred Family Surnames (single-char), plus common compound ones.
_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴鬱胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
    "欧阳太史端木上官司马东方独孤南宫万俟闻人夏侯诸葛尉迟公羊赫连澹台皇甫宗政濮阳公冶太叔申屠公孙慕容仲孙钟离长孙宇文司徒鲜于司空闾丘子车亓官司寇巫马公西颛孙壤驷公良漆雕乐正宰父谷梁拓跋夹谷轩辕令狐段干百里呼延东郭南门羊舌微生公仪梁丘左丘东门西门"
)

_SURNAME_RE = re.compile(
    "(" + "|".join(sorted(_SURNAMES, key=len, reverse=True)) + ")"
)

# Common words a name candidate must not be (over-match suppression).
_NAME_BLACKLIST = frozenset(
    "公司 机构 项目 包括 问题 情况 材料 文件 报告 内容 信息 数据 金额 合同 协议 条款 "
    "先生 女士 经理 董事长 总经理 负责人 甲方 乙方 联系人 代表 单位 产品 服务 客户 "
    "本人 他们 我们 你们 大家 东西 时候 地方 事情 国家 银行 学校 医院 人员 业务 "
    "经办人 担保人 被保证人 合作方 借款人 申请人 委托人 受托人 保证人 抵押人 出质人 "
    "权利人 义务人 投保人 被保险人 受益人 收款人 付款人 开户人 持卡人 签字人 "
    "管理人 托管人 代理人 经纪人 介绍人 中间人 中介人 联络人 法人 自然人 监护人 "
    "法定代表人 法人代表 董事会 监事会 总监 副总 董事 监事 股东 员工 用户 记者 "
    "医生 律师 老师 学生 人士 主管 专员 工程师 技术员 领导 同志 秘书 助理 财务 "
    "人事 行政 部门 账号 账户 卡号 手机 电话 邮箱 地址 日期 时间 经办 担保".split()
)

# Connectors/particles that end a name run ("合作方为张三与李四" → 张三).
_NAME_GIVEN_STOP = frozenset("与和及或等以及被把将让以向对从为是之的了着过我你他她它们这那其该")

# Context labels that strongly imply the following 2-3 CJK chars are a name,
# optionally separated by a connector ("联系人为张三").
_NAME_CONTEXT_RE = re.compile(
    r"(?:姓名|联系人|负责人|法定代表人|法人代表|签字人|经办人|担保人|被保证人"
    r"|合作方|借款人|申请人|委托人|受托人|保证人|抵押人|出质人|权利人|义务人"
    r"|投保人|被保险人|受益人|收款人|付款人|开户人|持卡人|董事|监事|总经理|董事长)"
    r"[:：\s]*(?:为|是|由|系)?([一-龥]{2,3})"
)

_CJK = "一-鿿"
_FLANK_RE = re.compile(rf"[\w{_CJK}]")

# Particles a name may directly follow ("合作方为张三", "由李四负责"); these
# count as a left boundary even though they're CJK.
_NAME_LEFT_PARTICLE = frozenset("为是由系叫称的和与被把将让以向对从")

# --------------------------------------------------------------------------
# Company-name heuristics (suffix dictionary + bounded backtrack)
# --------------------------------------------------------------------------

# Company-name tails. Legal-form suffixes (有限公司 / 集团 / 控股 ...) end a
# company name unconditionally. Standalone industry nouns (科技 / 银行 /
# 投资 ...) only end a name at a boundary or when a legal-form tail follows —
# otherwise "银行卡号"-style common-noun phrases become false positives.
# Generic nouns that usually appear mid-name (数据 / 网络 / 医疗 / 文化 ...)
# are excluded from the defaults: such companies end in 有限公司 anyway.
_COMPANY_LEGAL_TAILS = frozenset(
    "股份有限公司 有限责任公司 集团有限公司 有限公司 集团公司 集团 控股 股份 公司".split()
)
_COMPANY_SUFFIXES = _COMPANY_LEGAL_TAILS | frozenset(
    "科技 银行 证券 基金 保险 制造 建设 能源 投资 地产 置业 事务所 研究院".split()
)
# Name may keep going through one of these after an industry-noun suffix.
_COMPANY_CONTINUE = (
    "股份有限公司", "有限责任公司", "集团有限公司", "有限公司",
    "集团公司", "集团", "控股", "股份", "公司",
)

# Chars that, glued right onto an industry-noun suffix, show a compound noun
# rather than a company tail: "银行卡号" / "保险产品" / "银行业" / "基金会".
# Connectors (与/和/及/等) and verbs ("…证券签署") pass the edge check.
_COMPANY_NOUN_GLUE = frozenset("卡账号费业会局部所园网站馆心场区产品")

# Max head (name chars before the suffix) for industry-noun candidates; a longer
# backtrack means the suffix sits mid-sentence ("基金管理公司发行新基金").
_COMPANY_INDUSTRY_MAX_HEAD = 6

# Common-noun phrases that end in a legal tail but are not company names
# ("全资子公司", "证券公司", "网上银行"); exact-match rejection only, so real
# companies containing these words ("中信证券公司") still pass.
_COMPANY_NOUN_BLACKLIST = frozenset(
    "子公司 分公司 总公司 全资子公司 控股子公司 母公司 集团公司 上市公司 "
    "证券公司 基金管理公司 信托公司 租赁公司 咨询公司 保险公司 期货公司 "
    "网上银行 手机银行 电话银行 商业银行 人民银行 开发银行 投资基金 担保公司 融资公司".split()
)
# Phrases that never form a company's legal name but often appear inside a
# candidate ("讨论网上银行", "XX手机银行", "讨论全资子公司"): rejected as a
# *suffix* of the name, unlike the exact-match blacklist above. Deliberately
# excludes 商业银行 / 证券公司 / 总公司 / 投资基金 tails, which DO appear in
# real company names.
_COMPANY_SUFFIX_BLACKLIST = frozenset(
    "网上银行 手机银行 电话银行 子公司 上市公司".split()
)
_COMPANY_SUFFIX_RE = re.compile(
    "(" + "|".join(sorted(_COMPANY_SUFFIXES, key=len, reverse=True)) + ")"
)

# Deictic/modal prefixes that make a suffix match a false positive ("本公司").
# 贵 is *not* here — "贵州…" / "贵阳…" are legitimate company-name starts.
_COMPANY_PREFIX_BLACKLIST = ("本", "该", "某", "我", "你", "此", "这", "那", "其")

# Written as ONE string with explicit escapes: an earlier version had its
# curly quotes mangled to ASCII, which Python silently read as implicit string
# concatenation — the boundary set then lost “”‘’ and " entirely, so company
# names inside Chinese quotes were never detected.
_BOUNDARY_CHARS = " \t\n\r，。；！？、：:\"“”‘’'（）()[]{}〈〉《》·-—…,;!?"

# Max chars to backtrack from a company suffix to the name start.
_COMPANY_MAX_BACK = 36

# Single chars that cannot start a company name: conjunctions / prepositions /
# particles ("为", "由", "的", ...). The backward scan stops when it meets
# one, so "甲方为北京某某科技…" yields "北京某某科技" instead of dragging in
# the whole preceding sentence.
_COMPANY_PARTICLE_STOP = frozenset(
    "为是是由与和及或在向对从被把的地得着了过等也都就才还很将会能但而却则若如除之以至及"
)

# Role / deictic words that end a company-name scan: the name starts right
# after them ("甲方为…", "该公司…"). Checked as a variable-length window so
# both 2-char ("甲方") and 3-char ("某公司") roles are caught.
_COMPANY_ROLES = frozenset(
    "甲方 乙方 丙方 丁方 我方 贵方 他方 对方 "
    "该公司 本公司 我公司 贵公司 某公司".split()
)
_COMPANY_ROLE_LENS = max(len(w) for w in _COMPANY_ROLES)


class RuleDetector(TextDetector):
    """Detect sensitive fields via regex + heuristics (no LLM, fully offline)."""

    def __init__(
        self,
        specs: dict[str, FieldSpec] | None = None,
        fields: list[str] | None = None,
        *,
        verify_checksums: bool = True,
    ):
        self.specs = select_specs(specs or load_field_specs(), fields)
        self.verify_checksums = verify_checksums
        self._regex_specs = [s for s in self.specs.values() if s.compiled is not None]
        self._name_spec = self.specs.get("person_name")
        self._company_spec = self.specs.get("company_name")

    def detect(self, doc: ParsedDocument) -> list[Detection]:
        dets: list[Detection] = []
        for spec in self._regex_specs:
            dets.extend(self._detect_regex(doc, spec))
        if self._name_spec:
            dets.extend(self._detect_names(doc))
        if self._company_spec:
            dets.extend(self._detect_companies(doc))
        return sorted(dets, key=lambda d: (d.start, d.end))

    # -- structured (regex) fields ------------------------------------------

    def _detect_regex(self, doc: ParsedDocument, spec: FieldSpec) -> list[Detection]:
        out: list[Detection] = []
        assert spec.compiled is not None
        for m in spec.compiled.finditer(doc.text):
            value = m.group()
            if not self._passes(spec, value):
                continue
            out.append(
                self._make(doc, spec.name, value, m.start(), m.end(), spec.confidence)
            )
        return out

    def _passes(self, spec: FieldSpec, value: str) -> bool:
        if not self.verify_checksums:
            return True
        if spec.heuristic == "checksum_id" and not _valid_id_checksum(value):
            return False
        if spec.heuristic == "checksum_uscc" and not _valid_uscc_checksum(value):
            return False
        return True

    # -- person names ---------------------------------------------------------

    def _detect_names(self, doc: ParsedDocument) -> list[Detection]:
        spec = self._name_spec
        assert spec is not None
        text = doc.text
        out: list[Detection] = []

        # Round 1 — context labels ("联系人：张三"), high confidence.
        for m in _NAME_CONTEXT_RE.finditer(text):
            name = self._trim_name(m.group(1))
            if not name or not self._valid_name(name):
                continue
            end = m.start(1) + len(name)
            out.append(self._make(doc, spec.name, name, m.start(1), end, 0.95))

        # Round 2 — bare surname + 1-2 CJK chars, low confidence, must be bounded.
        for m in _SURNAME_RE.finditer(text):
            surname = m.group(1)
            start = m.start()
            tail = text[m.end(): m.end() + 2]
            run = self._first_cjk_run(tail)
            if not run:
                continue
            name = surname + run
            if not self._valid_name(name):
                continue
            end = start + len(name)
            if not self._is_bounded(text, start, end):
                continue
            out.append(self._make(doc, spec.name, name, start, end, 0.5))
        return out

    @staticmethod
    def _first_cjk_run(s: str) -> str:
        out = ""
        for ch in s:
            if not ("一" <= ch <= "鿿"):
                break
            if ch in _NAME_GIVEN_STOP:  # "张三的雇主" → 张三, not 张三的
                break
            out += ch
            if len(out) == 2:
                break
        return out

    @staticmethod
    def _trim_name(raw: str) -> str:
        """Drop trailing connectors/particles from a context-captured span
        ("合作方为张三与李四" → "张三")."""
        while raw and raw[-1] in _NAME_GIVEN_STOP:
            raw = raw[:-1]
        return raw

    @staticmethod
    def _valid_name(name: str) -> bool:
        if not 2 <= len(name) <= 4:
            return False
        if name in _NAME_BLACKLIST:
            return False
        if not all("一" <= ch <= "鿿" for ch in name):
            return False
        return name[0] in _SURNAMES

    @staticmethod
    def _is_bounded(text: str, start: int, end: int) -> bool:
        """The span [start, end) is flanked by boundaries (doc edge, space,
        punct), or by a particle on either side ("合作方为张三", "张三的雇主")."""
        left = text[start - 1] if start > 0 else ""
        right = text[end] if end < len(text) else ""
        left_ok = (
            left == "" or not _FLANK_RE.fullmatch(left) or left in _NAME_LEFT_PARTICLE
        )
        right_ok = (
            right == "" or not _FLANK_RE.fullmatch(right) or right in _NAME_LEFT_PARTICLE
        )
        return left_ok and right_ok

    # -- company names ---------------------------------------------------------

    def _detect_companies(self, doc: ParsedDocument) -> list[Detection]:
        spec = self._company_spec
        assert spec is not None
        text = doc.text
        cands: list[tuple[int, int, str]] = []  # (start, end, name)
        for m in _COMPANY_SUFFIX_RE.finditer(text):
            suffix_start, suffix_end = m.start(), m.end()
            suffix = m.group()
            start = self._company_start(text, suffix_start)
            if start < 0:
                continue
            if suffix not in _COMPANY_LEGAL_TAILS:
                # Industry noun: needs a short non-empty head ("银行卡号",
                # "基金管理公司发行新基金" both fail), and must not be glued
                # onto another noun ("保险产品").
                head = text[start:suffix_start]
                if not head or len(head) > _COMPANY_INDUSTRY_MAX_HEAD:
                    continue
                if not self._valid_company_edge(text, suffix_end):
                    continue
            name = text[start:suffix_end]
            if name in _COMPANY_NOUN_BLACKLIST:
                continue
            if any(name.endswith(t) for t in _COMPANY_SUFFIX_BLACKLIST):
                continue
            if not self._valid_company(name):
                continue
            cands.append((start, suffix_end, name))
        # Longest span first per start: "北京某某科技有限公司" (tail 有限公司)
        # beats "北京某某科技" (tail 科技) at the same start; contained hits
        # are skipped.
        cands.sort(key=lambda c: (c[0], -(c[1] - c[0]), c[1]))
        out: list[Detection] = []
        covered: list[tuple[int, int]] = []
        for start, end, name in cands:
            if any(s <= start and end <= e for s, e in covered):
                continue
            covered.append((start, end))
            out.append(self._make(doc, spec.name, name, start, end, 0.9))
        return out

    @staticmethod
    def _company_start(text: str, suffix_start: int) -> int:
        """Backtrack from the suffix to the name's start, stopping at a
        boundary, particle, or role word ("甲方为北京某某科技…" → 北京某某科技)."""
        limit = max(0, suffix_start - _COMPANY_MAX_BACK)
        i = suffix_start
        while i > limit:
            for k in range(min(_COMPANY_ROLE_LENS, i - limit), 1, -1):
                if text[i - k:i] in _COMPANY_ROLES:
                    return i
            left = text[i - 1]
            if left in _BOUNDARY_CHARS or left in _COMPANY_PARTICLE_STOP:
                return i
            i -= 1
        return i

    @staticmethod
    def _valid_company(name: str) -> bool:
        if not 2 <= len(name) <= 40:
            return False
        first = name[0]
        if not ("一" <= first <= "鿿") and not (
            "A" <= first <= "Z" or "a" <= first <= "z"
        ):
            return False
        if first.isdigit():
            return False
        if name[0] in _COMPANY_PREFIX_BLACKLIST:
            return False
        return True

    @staticmethod
    def _valid_company_edge(text: str, suffix_end: int) -> bool:
        """An industry-noun suffix is rejected only when a noun is glued right
        on ("银行卡号", "保险产品", "银行业协会"). Boundary, connector
        ("XX银行与…"), verb ("XX证券签署…") and legal-form continuations all
        pass, so real companies aren't dropped."""
        if suffix_end >= len(text):
            return True
        nxt = text[suffix_end]
        if nxt in _BOUNDARY_CHARS or nxt in "与和及或等以及":
            return True
        if any(text.startswith(t, suffix_end) for t in _COMPANY_CONTINUE):
            return True
        return nxt not in _COMPANY_NOUN_GLUE

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _make(
        doc: ParsedDocument,
        field_type: str,
        value: str,
        start: int,
        end: int,
        confidence: float,
    ) -> Detection:
        block = doc.block_at(start)
        return Detection(
            field_type=field_type,
            value=value,
            start=start,
            end=end,
            page=block.page if block else 1,
            source="rules",
            confidence=confidence,
            bbox=block.bbox if block else None,
        )
