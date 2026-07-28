# -*- coding: utf-8 -*-
"""
검증 전용 스크립트 (뉴스타파 데이터는 배포 금지, 대조용으로만 사용).

우리 전자관보 파이프라인 산출(종목별 보유 공직자)을, 같은 기준연도의 뉴스타파
'상장주식 명세'로 만든 집계와 대조해 파서 버그(뭉텅이/누락/오귀속)를 잡는다.

★ 기준연도 정합:
    · 뉴스타파 jaesan-Y      = 공개연도 Y = (Y-1)년말 기준.
    · 우리 file_year(파일명)  = 발행(공개)연도 - 1 = 기준연도.
  따라서 뉴스타파 jaesan-Y 는 '우리 기준연도 (Y-1)' 과 짝이 맞는다.
  (예: 뉴스타파 2025 ↔ 우리 기준연도 2024. 우리 최신 기준연도 2025는 뉴스타파 2026이 필요.)

뉴스타파는 관보 원문과 같은 명세 형식이나 PDF 깨짐이 없어, 우리 파서 자체의 건전성
(뭉텅이 재발 여부)도 함께 확인된다. 우리 쪽 집계는 cmd_parse 와 동일한 로직
(details_of + parse_securities + normalize)을 재사용해 만든다.

주의: 뉴스타파 관할기관은 정부/국회/대법원/선관위/헌재 전부지만, 우리 관보 파이프라인은
      정부/대법원/선관위만 수집한다. 기본은 그 셋으로 필터해 비교한다(--all 로 전체).

사용: python3 verify_against_newstapa.py [--news-year=2025] [--all] [--top=40]
"""
import sys, os, csv, io, re
from collections import defaultdict

import gwanbo_crawler as G
from holdings_parser import parse_securities, normalize

NEWS_DIR = "뉴스타파 데이터"
OURS_ORGANS = {"정부공직자윤리위원회", "대법원공직자윤리위원회", "중앙선거관리위원회공직자윤리위원회"}
# 뉴스타파 관할기관 라벨 → 우리 필터 집합 매핑(뉴스타파는 '…공직자윤리위원회' 표기)
NEWS_OURS_ORGANS = {"정부공직자윤리위원회", "대법원공직자윤리위원회",
                    "중앙선거관리위원회공직자윤리위원회"}


def newstapa_holdings(news_year, all_organs=False):
    """뉴스타파 상장주식 명세를 우리 파서로 파싱 → {정규화종목: set(공직자)}, {종목: 총주수}, 인원수."""
    p = os.path.join(NEWS_DIR, "newstapa-jaesan-%s" % news_year,
                     "CSV", "newstapa-jaesan-%s-records.csv" % news_year)
    if not os.path.exists(p):
        return None, None, 0
    data = open(p, encoding="utf-8-sig").read().replace("\x00", "")
    holders = defaultdict(set); shares = defaultdict(int); persons = set()
    for r in csv.DictReader(io.StringIO(data)):
        if r["재산의 종류"] != "상장주식":
            continue
        if not all_organs and r["관할기관"] not in NEWS_OURS_ORGANS:
            continue
        person = r["이름"]                       # 공직자 본인(친족 보유도 본인에 귀속)
        spec = (r["소재지 면적 등 권리의 명세"] or "")
        spec = re.sub(r"\s+", " ", spec).strip()  # details_of 와 동일하게 공백 정리
        counted = False
        for h in parse_securities(spec):
            if h.qty in (None, 0):                # 0주(전량매도) 제외(우리 집계와 동일)
                continue
            key = normalize(h.name)
            holders[key].add(person); shares[key] += int(h.qty); counted = True
        if counted:
            persons.add(person)
    return holders, shares, len(persons)


def our_holdings(base_year, all_organs=False):
    """우리 PDF에서 특정 기준연도의 종목별 보유 공직자 집합을 cmd_parse 와 동일 로직으로 산출.
    (all_organs 는 우리 파이프라인엔 무의미 — 관할기관 정보를 PDF에서 신뢰성 있게 못 뽑음.
     우리 수집 자체가 정부/대법원/선관위라 필터 불필요.)"""
    from pypdf import PdfReader
    holders = defaultdict(set); shares = defaultdict(int); persons = set()
    files = [f for f in os.listdir(G.PDF_DIR)
             if f.endswith(".pdf") and G.file_year(f) == base_year]
    for fn in files:
        try:
            r = PdfReader(os.path.join(G.PDF_DIR, fn))
        except Exception:
            continue
        person = fn
        for pg in r.pages:
            t = pg.extract_text() or ""; blob = t.replace("\n", " ")
            nm = G.NAMEONLY.search(blob)
            if nm:
                person = nm.group(1)
            hd = G.HEADER.search(blob)
            if hd:
                person = hd.group(3)
            for d in G.details_of(t):
                for h in parse_securities(d):
                    if h.qty in (None, 0):
                        continue
                    key = normalize(h.name)
                    holders[key].add(person); shares[key] += int(h.qty); persons.add(person)
    return holders, shares, len(persons), len(files)


def clumps(holders):
    """뭉텅이(콤마 포함 = 다종목 미분리) 종목키."""
    return [k for k in holders if "," in k]


def main():
    news_year = 2025          # 뉴스타파 최신 가용연도
    all_organs = False
    top = 40
    for a in sys.argv[1:]:
        if a.startswith("--news-year="):
            news_year = int(a.split("=", 1)[1])
        elif a == "--all":
            all_organs = True
        elif a.startswith("--top="):
            top = int(a.split("=", 1)[1])
    base_year = news_year - 1  # 정합되는 우리 기준연도

    nh, ns, np_ = newstapa_holdings(news_year, all_organs)
    if nh is None:
        print("뉴스타파 %s 데이터 없음." % news_year); return
    oh, osh, op_, nfiles = our_holdings(base_year, all_organs)

    scope = "전체 관할기관" if all_organs else "정부/대법원/선관위"
    print("── 검증: 뉴스타파 %s (기준 %d말) ↔ 우리 기준연도 %d ──"
          % (news_year, base_year, base_year))
    print("   범위: %s" % scope)
    print("   뉴스타파: 상장주식 보유 공직자 %d명 / 종목 %d개" % (np_, len(nh)))
    print("   우리    : PDF %d개 / 보유 공직자 %d명 / 종목 %d개" % (nfiles, op_, len(oh)))

    # [1] 파서 건전성: 깨끗한 뉴스타파 입력·우리 입력 모두 뭉텅이 0이어야 함
    nc, oc = clumps(nh), clumps(oh)
    print("\n[1] 파서 뭉텅이(다종목 미분리) 검사")
    print("    · 뉴스타파 입력: %s" % ("깨끗함 ✔" if not nc else "⚠ %d개" % len(nc)))
    for b in nc[:8]:
        print("        -", b[:80])
    print("    · 우리 입력    : %s" % ("깨끗함 ✔" if not oc else "⚠ %d개" % len(oc)))
    for b in oc[:8]:
        print("        -", b[:80])

    # [2] 종목별 보유인원 비교(뉴스타파 상위 top)
    ranked = sorted(nh.items(), key=lambda kv: -len(kv[1]))[:top]
    print("\n[2] 종목별 보유 공직자 수 (뉴스타파 상위 %d)" % top)
    print("    %-18s %8s %8s %8s" % ("종목", "뉴스타파", "우리", "차이"))
    for k, who in ranked:
        n = len(who); o = len(oh.get(k, ()))
        flag = "  ← 우리 누락 의심" if o == 0 else ("  ← 우리가 많음(확인)" if o > n + 2 else "")
        print("    %-18s %8d %8d %8d%s" % (k[:18], n, o, o - n, flag))

    # [3] 우리 상위 종목 중 뉴스타파에 없는 것(유령/오파싱 의심)
    ours_ranked = sorted(oh.items(), key=lambda kv: -len(kv[1]))[:80]
    ghosts = [(k, len(who)) for k, who in ours_ranked if k not in nh]
    print("\n[3] 우리 상위 종목 중 뉴스타파에 없음(유령/오파싱 의심): %s"
          % ("없음 ✔" if not ghosts else "%d개" % len(ghosts)))
    for k, c in ghosts[:20]:
        print("    - %-20s (우리 %d명)" % (k[:20], c))

    # [4] 전체 상관: 공통 종목의 보유인원 상관/일치도 요약
    common = [k for k in nh if k in oh]
    if common:
        import statistics
        ratios = [len(oh[k]) / len(nh[k]) for k in common if len(nh[k]) >= 5]
        if ratios:
            print("\n[4] 공통 종목(뉴스타파 보유≥5) %d개의 우리/뉴스타파 인원비 중앙값 %.2f"
                  % (len(ratios), statistics.median(ratios)))
            print("    (1.0에 가까울수록 일치. 우리가 국회/헌재 미포함이라 다소 <1 정상)")


if __name__ == "__main__":
    main()
