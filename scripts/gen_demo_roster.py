"""Generate a realistic demo workbook (员工花名册) for xlsx redaction testing.

Synthetic-but-realistic PII: valid GB11643-checksum ID numbers, plausible
phone/bank-card numbers, common name combinations. Nothing here belongs to a
real person. Adversarial structure: number-typed phones, text-typed phones,
hidden sheet, chart, PII comment, PII docProps, formula echo.

Usage: python scripts/gen_demo_roster.py [out.xlsx]
"""

from __future__ import annotations

import datetime as dt
import random
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment

SURNAMES = "张王李赵刘陈杨黄周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭"
GIVEN = [
    "伟", "芳", "娜", "敏", "静", "磊", "军", "洋", "勇", "艳", "杰", "娟",
    "涛", "明", "超", "秀英", "建国", "志强", "秀兰", "晓明", "雨轩", "思琪",
]
AREA = ["110101", "310104", "440305", "510107", "330106", "420111", "320115"]
ROADS = [
    ("北京市朝阳区望京街{}号", 1, 99), ("上海市徐汇区漕溪北路{}号", 1, 99),
    ("深圳市南山区科技园南路{}号", 1, 99), ("成都市武侯区天府大道{}号", 1, 99),
    ("杭州市西湖区文三路{}号", 1, 99), ("武汉市洪山区珞喻路{}号", 1, 99),
]
DEPTS = ["研发部", "市场部", "财务部", "人力部", "运营部", "法务部"]
PHONE_PREFIX = ["138", "139", "186", "187", "150", "135", "176", "177"]
BANK_PREFIX = ["621700", "622202", "622848", "621661"]  # plausible IIN-ish
WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
CHECK = "10X98765432"


def id_number(rng: random.Random) -> str:
    """A syntactically valid 18-digit ID (area + birth + GB11643 checksum)."""
    year = rng.randint(1975, 2000)
    birth = f"{year}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
    body = rng.choice(AREA) + birth + f"{rng.randint(0, 999):03d}"
    s = sum(int(c) * w for c, w in zip(body, WEIGHTS))
    return body + CHECK[s % 11]


def phone(rng: random.Random) -> str:
    return rng.choice(PHONE_PREFIX) + "".join(rng.choice("0123456789") for _ in range(8))


def bank_card(rng: random.Random) -> str:
    return rng.choice(BANK_PREFIX) + "".join(rng.choice("0123456789") for _ in range(13))


def address(rng: random.Random) -> str:
    tpl, lo, hi = rng.choice(ROADS)
    return tpl.format(rng.randint(lo, hi)) + f"{rng.randint(1,3)}单元{rng.randint(501,2204)}室"


def build(out: Path) -> None:
    rng = random.Random(20241103)  # deterministic roster
    wb = Workbook()
    ws = wb.active
    ws.title = "员工花名册"
    ws.append(
        ["工号", "姓名", "身份证号", "手机号", "银行卡号", "家庭住址",
         "部门", "入职日期", "月薪", "紧急联系人"]
    )
    by_dept: dict[str, list[float]] = {}
    for i in range(1, 41):
        name = rng.choice(SURNAMES) + rng.choice(GIVEN)
        dept = rng.choice(DEPTS)
        salary = round(rng.uniform(8000, 45000), 2)
        by_dept.setdefault(dept, []).append(salary)
        hire = dt.date(rng.randint(2015, 2024), rng.randint(1, 12), rng.randint(1, 28))
        emergency = f"{rng.choice(SURNAMES)}{rng.choice(GIVEN)} {phone(rng)}"
        row = [
            f"EMP{i:04d}", name, id_number(rng), phone(rng), bank_card(rng),
            address(rng), dept, hire, salary, emergency,
        ]
        ws.append(row)
        if i % 2:  # odd rows: phone as a *number* (classic export artifact)
            ws.cell(row=i + 1, column=4).value = int(row[3])
            ws.cell(row=i + 1, column=4).number_format = "0"
        ws.cell(row=i + 1, column=8).number_format = "yyyy-mm-dd"
        ws.cell(row=i + 1, column=9).number_format = "#,##0.00"
    ws["K1"] = '=CONCATENATE(B2,"的卡号尾号是",RIGHT(E2,4))'  # formula echo
    ws["C2"].comment = Comment(f"实名人:{ws['B2'].value}，备用电话 {phone(rng)}", "hr-系统")
    ws.freeze_panes = "A2"

    # department average-salary mini table + chart (chart survives redaction)
    stat_row = 1
    ws2 = wb.create_sheet("部门薪资统计")
    ws2.append(["部门", "平均月薪"])
    for dept in DEPTS:
        ws2.append([dept, round(sum(by_dept[dept]) / len(by_dept[dept]), 2)])
        stat_row += 1
    chart = BarChart()
    chart.title = "部门平均月薪"
    chart.add_data(Reference(ws2, min_col=2, min_row=1, max_row=stat_row), titles_from_data=True)
    chart.set_categories(Reference(ws2, min_col=1, min_row=2, max_row=stat_row))
    ws2.add_chart(chart, "D2")

    hidden = wb.create_sheet("调薪记录(机密)")
    hidden.sheet_state = "hidden"
    hidden.append(["姓名", "调整日期", "调后月薪", "审批人"])
    for _ in range(8):
        hidden.append(
            [
                rng.choice(SURNAMES) + rng.choice(GIVEN),
                dt.date(2024, rng.randint(1, 10), rng.randint(1, 28)),
                round(rng.uniform(9000, 50000), 2),
                rng.choice(SURNAMES) + rng.choice(GIVEN),
            ]
        )

    wb.properties.creator = "刘佳楠"
    wb.properties.lastModifiedBy = "刘佳楠"
    wb.properties.title = "2024员工花名册"
    wb.properties.company = "某某科技有限公司"
    wb.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/demo_xlsx/员工花名册.xlsx")
    target.parent.mkdir(parents=True, exist_ok=True)
    build(target)
