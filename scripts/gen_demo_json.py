"""Generate a realistic demo JSON (客户信息) for json redaction testing.

Synthetic-but-realistic PII, same generator family as ``gen_demo_roster``:
valid GB11643-checksum IDs, plausible phone/bank-card numbers. Adversarial
structure: number-typed phones mixed with text ones, a remark carrying two
numbers in one string, nested contact/order arrays, an empty object wedged
between list elements (page-shift trap), unicode keys. Nothing here belongs
to a real person.

Usage: python scripts/gen_demo_json.py [out.json]
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

SURNAMES = "张王李赵刘陈杨黄周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭"
GIVEN = [
    "伟", "芳", "娜", "敏", "静", "磊", "军", "洋", "勇", "艳", "杰", "娟",
    "涛", "明", "超", "秀英", "建国", "志强", "秀兰", "晓明", "雨轩", "思琪",
]
AREA = ["110101", "310104", "440305", "510107", "330106", "420111", "320115"]
ROADS = [
    "北京市朝阳区望京街{}号院{}号楼", "上海市徐汇区漕溪北路{}号{}幢",
    "深圳市南山区科技园南路{}号{}栋", "成都市武侯区天府大道{}号段{}座",
    "杭州市西湖区文三路{}号{}层",
]
CITIES = ["杭州", "上海", "深圳", "北京", "成都"]
DEPTS = ["研发部", "市场部", "财务部", "人力部", "运营部", "法务部"]
PHONE_PREFIX = ["138", "139", "186", "187", "150", "135", "176", "177"]
BANK_PREFIX = ["621700", "622202", "622848", "621661"]
GOODS = ["机械键盘", "显示器", "人体工学椅", "降噪耳机", "扩展坞", "扫描仪"]
WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
CHECK = "10X98765432"


def id_number(rng: random.Random) -> str:
    year = rng.randint(1975, 2000)
    birth = f"{year}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
    body = rng.choice(AREA) + birth + f"{rng.randint(0, 999):03d}"
    return body + CHECK[sum(int(c) * w for c, w in zip(body, WEIGHTS)) % 11]


def phone(rng: random.Random) -> str:
    return rng.choice(PHONE_PREFIX) + "".join(rng.choice("0123456789") for _ in range(8))


def bank_card(rng: random.Random) -> str:
    return rng.choice(BANK_PREFIX) + "".join(rng.choice("0123456789") for _ in range(13))


def name(rng: random.Random) -> str:
    return rng.choice(SURNAMES) + rng.choice(GIVEN)


def build(out: Path) -> None:
    rng = random.Random(20241108)  # deterministic export
    customers = []
    for i in range(1, 31):
        cust = {
            "客户编号": f"C{i:04d}",
            "姓名": name(rng),
            "证件号": id_number(rng),
            "手机号": phone(rng) if i % 2 else int(phone(rng)),  # mixed types
            "银行卡号": bank_card(rng),
            "地址": rng.choice(ROADS).format(rng.randint(1, 299), rng.randint(1, 9)),
            "所属部门": rng.choice(DEPTS),
            "联系人": [
                {"关系": "紧急联系人", "姓名": name(rng), "电话": phone(rng)},
                {"关系": "财务对接人", "姓名": name(rng), "电话": phone(rng)},
            ],
            "订单": [
                {
                    "单号": f"SO{rng.randint(10**8, 10**9 - 1)}",
                    "商品": rng.choice(GOODS),
                    "金额": round(rng.uniform(99, 19999), 2),
                    "数量": rng.randint(1, 5),
                }
                for _ in range(rng.randint(1, 4))
            ],
            "备注": f"短信 {phone(rng)} 已发，备用 {phone(rng)}",  # two hits, one string
        }
        customers.append(cust)
    customers.insert(15, {})  # empty element wedged mid-list (page-shift trap)

    doc = {
        "导出系统": "CRM-Export v2",
        "导出时间": "2024-11-08 09:30:00",
        "导出人": name(rng),
        "城市列表": CITIES,
        "客户列表": customers,
        "汇总": {"客户数": 30, "含空记录": 1, "完成率": 0.97},
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/demo_json/客户信息.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    build(target)
