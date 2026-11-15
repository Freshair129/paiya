"""Score synthetic B1a labels against the Collector's normal parsing path."""

import argparse
import json
from hashlib import sha256
from pathlib import Path

from paiya_collector.browser_adapter import VisiblePost, feed_from_visible_posts
from paiya_collector.runner import run_discovery

COLLECTOR = Path(__file__).resolve().parents[1]
DEFAULT_CASES = COLLECTOR / "fixtures" / "b1a-price-cases.jsonl"
# The owner confirmed all 100 expected labels in PR #111's review sheet.
OWNER_REVIEWED_CASES_SHA256 = "ad53f7fc5ba341f891e590a53d03e1ee55866fc0e2ac9916dac6967b938f4cb3"
GROUP = "synthetic-b1a-price"
OBSERVED_AT = "2026-11-15T00:00:00Z"
PRICE_FIELDS = ("amount", "currency", "item")


def score_cases(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    owner_reviewed = sha256(source.encode("utf-8")).hexdigest() == OWNER_REVIEWED_CASES_SHA256
    cases = [json.loads(line) for line in source.splitlines()]
    if len(cases) != 100 or len({case["id"] for case in cases}) != 100:
        raise ValueError("B1a requires 100 unique labelled cases")
    if len({case["text"] for case in cases}) != 100:
        raise ValueError("B1a requires 100 distinct fixture texts")
    measured = []
    score = dict.fromkeys((*PRICE_FIELDS, "joint"), 0)
    clear = controls = excluded = 0
    for case in cases:
        post = VisiblePost(
            permalink=f"https://www.facebook.com/groups/synthetic-b1a/posts/{case['id']}",
            post_text=case["text"],
        )
        posts = (post,) if case["access"] == "ok" else ()
        feed = feed_from_visible_posts(
            posts,
            sort_label="newest posts",
            access=case["access"],
            session_result="ok" if posts else "unknown",
            feed_end=True,
            scroll_step_seconds=0,
        )
        discovery = run_discovery(
            feed,
            fixture_path=path,
            group_key=GROUP,
            identity_scope=GROUP,
            observed_at=OBSERVED_AT,
            page_size=5,
            overlap=2,
            scroll_budget=1,
            time_budget_seconds=15,
            previously_published=set(),
        )
        each_asks = []
        for parsed in discovery.parsed_cards:
            for item in parsed.payload["items"]:
                if (
                    (price := item["price"])
                    and price["kind"] == "ask"
                    and price["unit"] == "each"
                    and price.get("amount_max") is None
                ):
                    each_asks.append(
                        {
                            "amount": price["amount"],
                            "currency": price["currency"],
                            "item": item["model"] if item["source_item_key"] is not None else None,
                        }
                    )
        expected = case["expected"]
        if expected is None:
            controls += 1
            passed = not each_asks and (bool(discovery.parsed_cards) == bool(posts))
            excluded += passed
        else:
            clear += 1
            actual = each_asks[0] if len(each_asks) == 1 else {}
            for field in PRICE_FIELDS:
                score[field] += actual.get(field) == expected[field]
            passed = actual == expected
            score["joint"] += passed
        measured.append(dict(case, actual=each_asks, passed=passed))

    if not clear or not controls:
        raise ValueError("B1a requires clear asking prices and exclusion controls")
    return {
        "label_status": "owner_reviewed" if owner_reviewed else "draft_awaiting_owner_review",
        "total": len(cases),
        "clear": clear,
        "controls": controls,
        "correct": score,
        "controls_excluded": excluded,
        "joint_percent": round(100 * score["joint"] / clear, 2),
        "meets_numeric_targets": 100 * score["joint"] >= 95 * clear and excluded == controls,
        "cases": measured,
    }


def _price_label(price: dict | None) -> str:
    if price is None:
        return "No comparable each-ask"
    return f"{price['item']} · {price['amount']} {price['currency']}"


def review_sheet(result: dict) -> str:
    label_status = (
        "The owner confirmed the expected labels for all 100 cases."
        if result["label_status"] == "owner_reviewed"
        else "The labels are awaiting owner review."
    )
    lines = [
        "# B1a price labels",
        "",
        "Review the expected column against each synthetic text.",
        "The parser result is supplied for diagnosis.",
        "Record any corrected label by case ID, or confirm the expected labels as written.",
        "",
        f"Clear prices matched jointly in {result['correct']['joint']}/{result['clear']} cases.",
        label_status,
        f"Exclusion controls matched in {result['controls_excluded']}/{result['controls']} cases.",
        "",
        "| Case | Synthetic text | Expected | Collector |",
        "|---|---|---|---|",
    ]
    for row in result["cases"]:
        actual = "; ".join(_price_label(price) for price in row["actual"]) or _price_label(None)
        lines.append(
            f"| {row['id']} | {row['text']} | {_price_label(row['expected'])} | {actual} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    result = score_cases(args.cases)
    if args.review:
        args.review.write_text(review_sheet(result), encoding="utf-8")
    if args.report:
        args.report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    summary = {key: value for key, value in result.items() if key != "cases"}
    summary["failed_case_ids"] = [case["id"] for case in result["cases"] if not case["passed"]]
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if result["meets_numeric_targets"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
