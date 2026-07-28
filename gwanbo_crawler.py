# -*- coding: utf-8 -*-
"""
전자관보(gwanbo.go.kr) 공직자 재산공개 자동 수급 크롤러  (파이썬 3.9+ 호환)
──────────────────────────────────────────────────────────────
개발자도구로 알아낸 실제 API 스펙 기반:
  · POST https://gwanbo.go.kr/SearchRestApi.jsp  (Form data)
  · query 안의 @@ORDER_NUM 은 '전체 카테고리'를 뜻하는 자리표시자 → 그대로 전송
  · 응답 JSON: { data: [ {category_name, count, list:[...], pageList}, ... ] }
  · 각 항목의 stored_pdf_file_path 로 PDF 주소 완성 (앞에 https://gwanbo.go.kr)

사용법 (맥 터미널, 세 파일 같은 폴더에서):
  pip3 install requests pypdf
  python3 gwanbo_crawler.py index      # 목록 수집 → gwanbo_index.json
  python3 gwanbo_crawler.py download    # 2026 정기 PDF 다운로드 → pdfs/
  python3 gwanbo_crawler.py parse       # 상장주식 순위 → holdings_ranking.csv
  python3 gwanbo_crawler.py all         # 위 3개 한 번에
"""

import sys, os, json, time, re, datetime
import requests

BASE = "https://gwanbo.go.kr"
API = BASE + "/SearchRestApi.jsp"
DOWNLOAD_API = BASE + "/user/common/ofcttCntntDownload.do"  # 통짜 PDF 다운로드(POST)

# 개발자도구에서 캡처한 실제 쿼리. @@ORDER_NUM 은 '전체'를 의미하므로 그대로 둔다.
QUERY = ("unstored_field_subject:("
         "(정부공직자 AND 재산공개) OR (정부공직자 AND 재산변동) OR (정부공직자 AND 재산등록) OR "
         "(대법원 AND 재산변동) OR (대법원 AND 재산등록) OR "
         "(중앙선거관리위원회 AND 재산변동) OR (중앙선거관리위원회 AND 재산등록) OR "
         "(중앙선거관리위원회 AND 재산)) "
         "AND keyword_category_order:(@@ORDER_NUM)")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Referer": BASE + "/user/search/searchThema.do?tabType=1",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE,
}

INDEX_FILE = "gwanbo_index.json"
PDF_DIR = "pdfs"
DELAY = 0.6
LIST_SIZE = 100     # 한 페이지당 항목 수(카테고리별)
MAX_PAGES = 400     # 안전 상한


def call_api(page_no, list_size=LIST_SIZE, retries=3):
    """한 페이지 요청 (쿼리는 원본 그대로, @@ORDER_NUM 유지)."""
    form = {"mode": "theme", "index": "gwanbo", "query": QUERY,
            "pQuery_tmp": "", "pageNo": str(page_no),
            "listSize": str(list_size), "sort": ""}
    for attempt in range(retries):
        try:
            r = requests.post(API, data=form, headers=HEADERS, timeout=25)
            r.raise_for_status()
            return json.loads(r.text)   # content-type이 text/json 이라 loads 사용
        except Exception as e:
            if attempt == retries - 1:
                print("   ! 요청 실패 (page=%s): %s" % (page_no, e))
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def item_key(it):
    return it.get("stored_pdf_file_path") or it.get("stored_org_file_seq") or it.get("search_key")


def content_seq(it):
    """다운로드에 쓸 cntnt_seq_no 추출.
    우선순위: stored_toc_seq → stored_field_url 안의 tocId= 값."""
    seq = it.get("stored_toc_seq")
    if seq:
        return seq
    url = it.get("stored_field_url", "") or ""
    m = re.search(r"tocId=([^&]+)", url)
    return m.group(1) if m else None


def summarize_counts(j):
    cats, total = [], 0
    for c in (j.get("data") or []):
        cnt = int(c.get("count") or 0)
        if cnt > 0:
            cats.append((c.get("category_name"), cnt))
            total += cnt
    return cats, total


# ── 1) 목록 수집 ────────────────────────────────────────────
def cmd_index():
    print("· 카테고리/건수 파악 중...")
    first = call_api(1, list_size=1)
    if not first:
        print("  API 응답 없음. 네트워크를 확인하세요."); return
    cats, total = summarize_counts(first)
    if total == 0:
        print("  결과 0건. 쿼리 또는 사이트 상태를 확인하세요.")
        print("  (참고) 받은 data 카테고리:", [c.get("category_name") for c in first.get("data", [])])
        return
    for n, cnt in cats:
        print("    - %s: %d건" % (n, cnt))
    print("  합계 %d건\n· 페이지 수집 시작 (listSize=%d)..." % (total, LIST_SIZE))

    all_items = {}
    for page in range(1, MAX_PAGES + 1):
        j = call_api(page, LIST_SIZE)
        if not j:
            break
        new = 0
        for c in (j.get("data") or []):
            for it in (c.get("list") or []):
                k = item_key(it)
                if k and k not in all_items:
                    all_items[k] = it; new += 1
        print("   page %d: +%d (누적 %d/%d)" % (page, new, len(all_items), total))
        if new == 0:
            break
        if len(all_items) >= total:
            break
        time.sleep(DELAY)

    items = list(all_items.values())
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print("\n\u2714 목록 저장: %s (%d건)" % (INDEX_FILE, len(items)))
    years = {}
    for it in items:
        y = it.get("keyword_field_year", "?")
        years[y] = years.get(y, 0) + 1
    print("  연도별:", dict(sorted(years.items(), reverse=True)))
    if len(items) < total:
        print("  ⚠ 수집(%d) < 총건수(%d): 페이지네이션이 예상과 다를 수 있음." % (len(items), total))
        print("    이 로그를 그대로 복사해서 알려주시면 방식을 맞춰 고쳐드립니다.")


# ── 2) PDF 다운로드 ─────────────────────────────────────────
def want(it, take_all, yfrom, yto, only_regular):
    if it.get("stored_file_type", "").lower() != "pdf":
        return False
    if it.get("stored_service_yn", "Y") != "Y":
        return False
    if take_all:
        return True
    y = it.get("keyword_field_year")
    try:
        yi = int(y)
    except (TypeError, ValueError):
        return False
    if yfrom and yi < yfrom:
        return False
    if yto and yi > yto:
        return False
    if only_regular:
        subj = (it.get("keyword_field_subject", "") or "") + (it.get("stored_field_subject", "") or "")
        # 정부: '정기재산변동'/'재산변동신고' · 대법원: '재산등록(변동)사항공개' ·
        # 선관위: '재산 공개대상자 재산등록/변동사항 신고내용 공개' — 표기가 제각각이라 폭넓게 허용
        ok = ("정기" in subj or "재산변동신고" in subj or "재산등록(변동)" in subj
              or "재산공개" in subj
              or ("공개대상자" in subj and "재산" in subj)
              or ("재산등록" in subj and "공개" in subj))
        if not ok:
            return False
    return True


def cmd_download(argv):
    take_all = "--all" in argv
    yfrom = yto = None
    for a in argv:
        if a.startswith("--year="):
            yfrom = yto = int(a.split("=", 1)[1])
        elif a.startswith("--from="):
            yfrom = int(a.split("=", 1)[1])
        elif a.startswith("--to="):
            yto = int(a.split("=", 1)[1])
    if yfrom is None and yto is None and not take_all:
        yfrom = yto = 2026  # 기본: 2026 정기공개
    only_regular = not take_all

    if not os.path.exists(INDEX_FILE):
        print("%s 없음. 먼저 'index' 를 실행하세요." % INDEX_FILE); return
    items = json.load(open(INDEX_FILE, encoding="utf-8"))
    targets = [it for it in items if want(it, take_all, yfrom, yto, only_regular)]
    os.makedirs(PDF_DIR, exist_ok=True)
    rng = "전체" if take_all else ("%s~%s" % (yfrom or "", yto or ""))
    print("다운로드 대상 %d건 (연도 %s, 전체 %d건 중) → %s/" % (len(targets), rng, len(items), PDF_DIR))
    ok = skip = fail = 0
    dl_headers = dict(HEADERS)
    dl_headers["Referer"] = BASE + "/user/search/searchThema.do?tabType=1"
    for i, it in enumerate(targets, 1):
        seq = content_seq(it)
        if not seq:
            fail += 1
            print("   [%d/%d] ✗ cntnt_seq_no 없음 (%s)" % (i, len(targets), it.get("keyword_field_subject", "")[:30]))
            continue
        organ = re.sub(r"[^\w가-힣]", "", it.get("stored_organ_nm", "org"))[:20]
        eno = it.get("keyword_ebook_no", "0")
        reg = it.get("keyword_field_regdate", "")
        uniq = str(seq)[-13:]  # cntnt_seq_no 뒤 13자리로 고유성 확보
        dest = os.path.join(PDF_DIR, "%s_%s_%s_%s.pdf" % (eno, organ, reg, uniq))
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            skip += 1; continue
        try:
            # 통짜 PDF 다운로드: POST cntnt_seq_no
            r = requests.post(DOWNLOAD_API, data={"cntnt_seq_no": seq},
                              headers=dl_headers, timeout=90)
            r.raise_for_status()
            content = r.content
            # 응답이 PDF인지 확인 (아니면 에러 페이지일 수 있음)
            if not content[:4] == b"%PDF":
                fail += 1
                print("   [%d/%d] ✗ %s: PDF 아님(%d bytes) — seq=%s"
                      % (i, len(targets), os.path.basename(dest), len(content), seq))
                continue
            with open(dest, "wb") as f:
                f.write(content)
            ok += 1
            print("   [%d/%d] \u2714 %s (%dKB)" % (i, len(targets), os.path.basename(dest), len(content)//1024))
        except Exception as e:
            fail += 1
            print("   [%d/%d] \u2717 %s: %s" % (i, len(targets), os.path.basename(dest), e))
        time.sleep(DELAY)
    print("\n완료: 성공 %d / 건너뜀 %d / 실패 %d" % (ok, skip, fail))


# ── 2b) 국회공보(국회의원 재산공개) 다운로드 ─────────────────
# 국회는 관보가 아닌 국회공보에 공개(공직자윤리법 §10). assembly.go.kr 공보 목록에서 확인한
# 재산공개 호의 첨부파일 ID. (목록: /portal/cnts/cntsNamgzn/gongbo.do, 검색어 '재산공개')
ASSEMBLY_ISSUES = [
    # (발행일 YYYYMMDD, 호수 라벨, atchFileId)
    ("20260326", "2026-54",  "0b500c662c0b4a57928afa682468b754"),
    ("20250327", "2025-51",  "1566ee279b40430c9faedcc0aec17268"),
    ("20240829", "2024-107", "02760d56f3434a2cab9f5c477a4b46c9"),
    ("20240328", "2024-36",  "959e13ad361144f6a6cb8289718474ad"),
    ("20230331", "2023-54",  "yavxaifcot5en3h548549wssqga70kwc"),
    ("20220331", "2022-31",  "ldxlh5e853jcc9yoe05qoub4ju6wlwz0"),
    ("20210325", "2021-42",  "ljiydqx6lf6qen8xqxkr4jw3rlpbtrw1"),
    ("20200828", "2020-98",  "s0dj2q0sl24eo8i90ke4uadobftmufw5"),
    ("20200326", "2020-36",  "gtwztinej6vorph6b8j7fxoxqx41nzyn"),
]
ASSEMBLY_DOWN = "https://www.assembly.go.kr/portal/cmmn/file/fileDown.do?atchFileId=%s&fileSn=1"


def cmd_assembly():
    """국회공보 재산공개 호 PDF를 pdfs/에 다운로드(파일명은 관보와 동일 규칙으로 file_year 호환)."""
    os.makedirs(PDF_DIR, exist_ok=True)
    h = {"User-Agent": HEADERS["User-Agent"],
         "Referer": "https://www.assembly.go.kr/portal/cnts/cntsNamgzn/gongbo.do"}
    ok = skip = fail = 0
    for reg, label, atch in ASSEMBLY_ISSUES:
        dest = os.path.join(PDF_DIR, "gukhoe%s_국회_%s_%s.pdf" % (label.replace("-", ""), reg, atch[:13]))
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            skip += 1
            continue
        try:
            r = requests.get(ASSEMBLY_DOWN % atch, headers=h, timeout=180)
            r.raise_for_status()
            if r.content[:4] != b"%PDF":
                fail += 1
                print("  ✗ %s: PDF 아님(%d bytes)" % (label, len(r.content)))
                continue
            open(dest, "wb").write(r.content)
            ok += 1
            print("  ✔ 국회공보 %s (%dKB)" % (label, len(r.content) // 1024))
        except Exception as e:
            fail += 1
            print("  ✗ %s: %s" % (label, e))
        time.sleep(DELAY)
    print("국회공보 완료: 성공 %d / 건너뜀 %d / 실패 %d → python3 gwanbo_crawler.py parse 로 반영" % (ok, skip, fail))


# ── 2c) 뉴스·공시 수집 (네이버 뉴스 검색 API + DART 오픈API) ─────────
# 저작권 안전 원칙: 기사 '제목+원문 링크+날짜'만 저장·표시(본문/요약 전재 금지).
# api_keys.json 형식: {"naver_client_id":"...","naver_client_secret":"...","dart_api_key":"..."}
API_KEYS_FILE = "api_keys.json"
NEWS_FILE = "news.json"


def _load_keys():
    if os.path.exists(API_KEYS_FILE):
        try:
            return json.load(open(API_KEYS_FILE, encoding="utf-8"))
        except Exception as e:
            print("api_keys.json 파싱 실패:", e)
    return {}


def cmd_news():
    """상위 종목·주요 인물의 최신 뉴스(제목/링크)와 국내 상위 종목 최근 공시를 수집 → news.json"""
    keys = _load_keys()

    def _valid(k):
        return bool(k) and k.isascii() and "여기에" not in k
    nid, nsec = keys.get("naver_client_id"), keys.get("naver_client_secret")
    dart = keys.get("dart_api_key")
    if not (_valid(nid) and _valid(nsec)):
        nid = nsec = None
    if not _valid(dart):
        dart = None
    if not os.path.exists("news_targets.json"):
        print("news_targets.json 없음 — 먼저 parse 를 실행하세요."); return
    tg = json.load(open("news_targets.json", encoding="utf-8"))
    out = {"stocks": {}, "people": {}, "filings": {},
           "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}

    def naver_news(q, n=3):
        import urllib.parse as up
        r = requests.get("https://openapi.naver.com/v1/search/news.json",
                         params={"query": q, "display": n, "sort": "date"},
                         headers={"X-Naver-Client-Id": nid, "X-Naver-Client-Secret": nsec},
                         timeout=15)
        r.raise_for_status()
        items = []
        for it in r.json().get("items", []):
            t = re.sub(r"<[^>]+>", "", it.get("title", ""))
            t = t.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            d = (it.get("pubDate", "") or "")[5:16]          # '21 Jul 2026'
            items.append({"t": t, "l": it.get("originallink") or it.get("link"), "d": d})
        return items

    if nid and nsec:
        print("· 네이버 뉴스 수집: 종목 %d + 인물 %d" % (len(tg["stocks"]), len(tg["people"])))
        for i, k in enumerate(tg["stocks"], 1):
            try:
                out["stocks"][k] = naver_news("%s 주가" % k)
            except Exception as e:
                print("  !", k, e)
            time.sleep(0.15)
        for p in tg["people"]:
            q = ("%s %s" % (p["n"], p["o"].split()[0])) if p.get("o") else p["n"]
            try:
                out["people"][p["n"]] = naver_news(q)
            except Exception as e:
                print("  !", p["n"], e)
            time.sleep(0.15)
    else:
        print("(뉴스 건너뜀) api_keys.json에 naver_client_id / naver_client_secret 를 넣어주세요.")

    if dart:
        try:
            import zipfile, io as _io
            import xml.etree.ElementTree as ET
            import holdings_parser as hp
            load_krx()
            print("· DART 고유번호 목록 다운로드...")
            r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml",
                             params={"crtfc_key": dart}, timeout=90)
            z = zipfile.ZipFile(_io.BytesIO(r.content))
            root = ET.fromstring(z.read(z.namelist()[0]).decode("utf-8"))
            sc2cc = {}
            for el in root.iter("list"):
                sc = (el.findtext("stock_code") or "").strip()
                if sc:
                    sc2cc[sc] = el.findtext("corp_code")
            bgn = (datetime.datetime.now() - datetime.timedelta(days=120)).strftime("%Y%m%d")
            cnt = 0
            for k in tg["stocks"]:
                code = hp.KRX_LISTED.get(hp.normalize(k))
                cc = sc2cc.get(code or "")
                if not cc:
                    continue
                rr = requests.get("https://opendart.fss.or.kr/api/list.json",
                                  params={"crtfc_key": dart, "corp_code": cc,
                                          "bgn_de": bgn, "page_count": 5}, timeout=30)
                rows = (rr.json().get("list") or [])[:3]
                if rows:
                    out["filings"][k] = [{"t": x.get("report_nm", ""), "d": x.get("rcept_dt", ""),
                                          "l": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + x.get("rcept_no", "")}
                                         for x in rows]
                    cnt += 1
                time.sleep(0.15)
            print("· DART 공시 확보: %d종목" % cnt)
        except Exception as e:
            print("  DART 실패:", e)
    else:
        print("(공시 건너뜀) api_keys.json에 dart_api_key 를 넣어주세요.")

    json.dump(out, open(NEWS_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    print("✔ 저장: %s (종목뉴스 %d · 인물뉴스 %d · 공시 %d) → parse 재실행으로 화면 반영"
          % (NEWS_FILE, len(out["stocks"]), len(out["people"]), len(out["filings"])))


# ── 3) 상장주식 추출 ────────────────────────────────────────
def _krx_from_kind():
    """kind.krx 상장법인목록(HTML). OTP 불필요라 봇 차단이 약함. {정규화명: 코드}.
    셀 순서: 회사명 / 시장구분 / 종목코드 / 업종 ... → 코드는 '6자리 숫자 셀'로 자동탐지."""
    import holdings_parser as hp
    url = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
    h = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    r = requests.get(url, headers=h, timeout=40)
    r.encoding = "euc-kr"
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 3:
            continue
        name = cells[0]
        # 6자리 숫자인 셀을 종목코드로 채택
        code = next((c for c in cells[1:] if re.fullmatch(r"\d{6}", c)), None)
        if name and code:
            out[hp.normalize(name)] = code
    return out


def _krx_from_data_krx():
    """data.krx.co.kr 최신 전종목: OTP 발급 → CSV. {정규화명: 코드}. 컬럼명 자동감지."""
    import csv, io
    import holdings_parser as hp
    otp_url = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
    dn_url = "http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
    h = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
         "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101"}
    otp_params = {"mktId": "ALL", "share": "1", "csvxls_isNo": "false",
                  "name": "fileDown", "url": "dbms/MDC/STAT/standard/MDCSTAT01901"}
    s = requests.Session()
    otp = s.get(otp_url, params=otp_params, headers=h, timeout=30).text
    r = s.post(dn_url, data={"code": otp}, headers=h, timeout=60)
    r.encoding = "euc-kr"
    rd = csv.DictReader(io.StringIO(r.text))
    cols = rd.fieldnames or []
    # 코드/이름 컬럼 자동 감지
    code_col = next((c for c in cols if "단축코드" in c), None) \
        or next((c for c in cols if "종목코드" in c), None)
    name_col = next((c for c in cols if "약명" in c), None) \
        or next((c for c in cols if ("한글" in c and "종목명" in c)), None) \
        or next((c for c in cols if "종목명" in c), None)
    out = {}
    for row in rd:
        code = (row.get(code_col) or "").strip() if code_col else ""
        name = (row.get(name_col) or "").strip() if name_col else ""
        if re.fullmatch(r"\d{6}", code) and name:
            out[hp.normalize(name)] = code
    return out


def _krx_from_github():
    """폴백: GitHub 구버전 목록(2018~2022)."""
    import gzip, csv, io
    import holdings_parser as hp
    m = {}
    try:
        r = requests.get("https://github.com/FinanceData/stock_master/raw/master/stock_master.csv.gz", timeout=60)
        r.raise_for_status()
        for row in csv.DictReader(io.StringIO(gzip.decompress(r.content).decode("utf-8", "ignore"))):
            nm = row.get("Name"); sym = row.get("Symbol")
            if nm and sym and str(row.get("Listing", "")).lower() == "true":
                m[hp.normalize(nm)] = sym
    except Exception as e:
        print("  (경고) stock_master 실패:", e)
    try:
        r = requests.get("https://raw.githubusercontent.com/corazzon/finance-data-analysis/main/krx.csv", timeout=60)
        r.raise_for_status()
        for row in csv.DictReader(io.StringIO(r.content.decode("utf-8", "ignore"))):
            nm = row.get("Name"); sym = row.get("Symbol")
            if nm and sym:
                m.setdefault(hp.normalize(nm), sym)
    except Exception as e:
        print("  (경고) krx.csv 실패:", e)
    return m


def load_krx(force=False):
    """국내/해외 분류용 KRX 사전 로드. KRX 최신 우선, 실패 시 GitHub 폴백.
    RECENT(신규·개명주)는 항상 덧씌움. force=True면 캐시 무시하고 재구축."""
    import holdings_parser as hp
    cache = "krx_map.json"
    if os.path.exists(cache) and not force:
        m = json.load(open(cache, encoding="utf-8"))
        print("· KRX 사전 로드(캐시): %d종목  (최신 갱신: python3 gwanbo_crawler.py krx)" % len(m))
    else:
        print("· KRX 최신 상장목록 다운로드 중 (kind.krx.co.kr)...")
        m = {}
        try:
            m = _krx_from_kind()
        except Exception as e:
            print("  kind.krx 실패:", e)
        if len(m) < 1000:
            print("· kind.krx 응답 부족(%d). data.krx 재시도..." % len(m))
            try:
                m2 = _krx_from_data_krx()
                if len(m2) > len(m):
                    m = m2
            except Exception as e:
                print("  data.krx 실패:", e)
        if len(m) >= 1000:
            print("· KRX 최신 목록 확보: %d종목" % len(m))
            # 우선주 등 kind.krx에 빠진 종목을 GitHub 목록으로 보강 (덮어쓰지 않음)
            try:
                gh = _krx_from_github()
                before = len(m)
                for k, v in gh.items():
                    m.setdefault(k, v)
                print("· 우선주 등 보강: +%d (GitHub)" % (len(m) - before))
            except Exception as e:
                print("  보강 실패:", e)
        else:
            print("· KRX 응답 부족(%d). GitHub 구버전 목록으로 폴백." % len(m))
            m = _krx_from_github()
            print("· GitHub 목록: %d종목" % len(m))
        json.dump(m, open(cache, "w", encoding="utf-8"), ensure_ascii=False)

    # 2021년 이후 신규상장/개명주 수동 보강 (구버전 폴백 대비) — 항상 적용
    RECENT = {
        "두산에너빌리티": "034020", "LG에너지솔루션": "373220", "POSCO홀딩스": "005490",
        "포스코홀딩스": "005490", "포스코": "005490", "카카오뱅크": "323410", "한화오션": "042660",
        "에코프로비엠": "247540", "에코프로": "086520", "카카오페이": "377300",
        "SK바이오사이언스": "302440", "SK아이이테크놀로지": "361610", "크래프톤": "259960",
        "하이브": "352820", "HD현대중공업": "329180", "HD한국조선해양": "009540",
        "HD현대일렉트릭": "267260", "HD현대": "267250", "SK스퀘어": "402340",
        "카카오게임즈": "293490", "펄어비스": "263750", "코스모신소재": "005070",
        "포스코퓨처엠": "003670", "포스코인터내셔널": "047050", "한화에어로스페이스": "012450",
        "현대로템": "064350", "LIG넥스원": "079550", "삼성바이오로직스": "207940",
        "SK바이오팜": "326030", "NAVER": "035420", "네이버": "035420",
        # KRX 다운로드 목록에 빠진 종목 수동 보강(코드는 네이버 금융 조회로 확인)
        "미래에셋증권2우B": "00680K",   # 우선주(보통주 006800과 별도)
        "한화비전": "489790",           # 구 한화인더스트리얼솔루션즈(사명 변경)
        "그린광학": "0015G0",           # 코스닥
    }
    for nm, code in RECENT.items():
        m[hp.normalize(nm)] = code
    hp.KRX_LISTED.clear(); hp.KRX_LISTED.update(m)


PRICE_FILE = "prices.json"
BASE_DATE = "20251230"  # 재산공개 기준일에 가장 가까운 거래일(2025년 마지막 거래일)


def fetch_price(code):
    """네이버 siseJson 한 번(2015~현재)으로 다시점 종가 추출.
    반환: dict(p2015, p2020, p2025, cur) — 각 연말 마지막 거래일 종가 + 현재가."""
    h = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
         "Referer": "https://finance.naver.com"}
    end = datetime.datetime.now().strftime("%Y%m%d")
    try:
        u = ("https://api.finance.naver.com/siseJson.naver?symbol=%s&requestType=1"
             "&startTime=20151201&endTime=%s&timeframe=day" % (code, end))
        r = requests.get(u, headers=h, timeout=20)
        rows = re.findall(r'\["(\d{8})",\s*[\d.]+,\s*[\d.]+,\s*[\d.]+,\s*(\d+)', r.text)
        if not rows:
            return None
        rows.sort()
        closes = {d: int(c) for d, c in rows}

        def year_end_close(year):
            # 해당 연도 12월의 마지막 거래일 종가
            ds = sorted(d for d in closes if d.startswith("%d12" % year))
            return closes[ds[-1]] if ds else None

        return {
            "p2015": year_end_close(2015),
            "p2020": year_end_close(2020),
            "p2025": year_end_close(2025),
            "cur": int(rows[-1][1]),
        }
    except Exception:
        return None


def cmd_price(argv):
    """상위 종목 시세 수집 → prices.json. 기본 상위 200개(국내)."""
    topn = 200
    for a in argv:
        if a.startswith("--top="):
            topn = int(a.split("=", 1)[1])
    if not os.path.exists("holdings_domestic.csv"):
        print("holdings_domestic.csv 없음. 먼저 parse 하세요."); return
    load_krx()
    import holdings_parser as hp
    # 국내 상위 종목명 → 코드
    names = []
    with open("holdings_domestic.csv", encoding="utf-8-sig") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split(",")
            if len(parts) >= 2:
                names.append(parts[1])
    names = names[:topn]
    prices = json.load(open(PRICE_FILE, encoding="utf-8")) if os.path.exists(PRICE_FILE) else {}
    print("시세 수집: 상위 %d종목 (네이버 금융, 2015말~현재)" % len(names))
    ok = 0
    for i, nm in enumerate(names, 1):
        code = hp.KRX_LISTED.get(hp.normalize(nm))
        if not code:
            continue
        if nm in prices and prices[nm].get("p2025"):
            continue
        p = fetch_price(code)
        if not p:
            prices[nm] = {"code": code, "cur": None}
        else:
            cur, base = p.get("cur"), p.get("p2025")
            rate = round((cur - base) / base * 100, 1) if (cur and base) else None
            prices[nm] = {"code": code, "cur": cur,
                          "p2015": p.get("p2015"), "p2020": p.get("p2020"),
                          "p2025": p.get("p2025"), "rate": rate}
            if cur:
                ok += 1
        if i % 20 == 0:
            pr = prices.get(nm, {})
            print("   %d/%d ... (%s %s원, %s%%)"
                  % (i, len(names), nm, format(pr.get("cur") or 0, ","), pr.get("rate")))
            json.dump(prices, open(PRICE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
        time.sleep(0.25)
    json.dump(prices, open(PRICE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    print("\u2714 국내 시세 저장: %s (%d종목 현재가 확보)" % (PRICE_FILE, ok))

    # ── 해외 종목 시세 (yfinance) → 원화 환산 ──
    fetch_overseas_prices(prices, topn)
    json.dump(prices, open(PRICE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    print("→ python3 gwanbo_crawler.py parse 로 화면에 반영하세요.")


def fetch_overseas_prices(prices, topn=200):
    """해외 상위 종목의 2025말 종가·현재가를 yfinance로 받아 원화 환산 저장."""
    try:
        import warnings; warnings.filterwarnings("ignore")
        import yfinance as yf
    except Exception:
        print("  (해외 시세 건너뜀) yfinance 미설치 — pip3 install yfinance 후 재실행")
        return
    import holdings_parser as hp
    if not os.path.exists("holdings_overseas.csv"):
        return
    names = []
    with open("holdings_overseas.csv", encoding="utf-8-sig") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split(",")
            if len(parts) >= 2:
                names.append(parts[1])
    names = names[:topn]

    def close_at(ticker, start, end):
        try:
            d = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
            return float(d["Close"].iloc[-1]) if len(d) else None
        except Exception:
            return None

    # 원달러 환율 (2025말 / 현재)
    fx25 = close_at("KRW=X", "2025-12-20", "2026-01-05") or 1443.0
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    fxnow = close_at("KRW=X", (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d"), today) or fx25
    print("· 해외 시세 수집 (yfinance) | 환율 2025말=%.0f, 현재=%.0f" % (fx25, fxnow))

    cnt = 0
    for nm in names:
        tk = hp.OVERSEAS_TICKER.get(nm)
        if not tk:
            continue
        if nm in prices and prices[nm].get("usd25"):
            continue
        # 달러 종가: 15/20/25년말 + 현재
        u15 = close_at(tk, "2015-12-20", "2016-01-05")
        u20 = close_at(tk, "2020-12-20", "2021-01-05")
        u25 = close_at(tk, "2025-12-20", "2026-01-05")
        unow = close_at(tk, (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d"), today)
        if not u25:
            continue
        # 원화 환산(비중 계산용) — 기준일 환율 사용
        p2025 = int(u25 * fx25)
        cur_krw = int(unow * fxnow) if unow else None
        rate = round((unow - u25) / u25 * 100, 1) if (unow and u25) else None
        prices[nm] = {
            "code": tk, "overseas": True,
            "p2025": p2025, "cur": cur_krw, "rate": rate,   # 원화(비중·내부계산용)
            # 달러 원본(화면 표시용)
            "usd15": round(u15, 2) if u15 else None,
            "usd20": round(u20, 2) if u20 else None,
            "usd25": round(u25, 2) if u25 else None,
            "usdcur": round(unow, 2) if unow else None,
        }
        cnt += 1
        print("   %s(%s) $%s→$%s (%s%%)"
              % (nm, tk, u25 and round(u25, 2), unow and round(unow, 2), rate))
    print("\u2714 해외 %d종목 시세 확보 (달러 표기)" % cnt)


NAV_CSS = (" .topnav{display:flex;gap:4px;margin:0 0 18px;flex-wrap:wrap}"
           " .topnav a{padding:6px 14px;border:1px solid var(--line);border-radius:99px;"
           "background:#fff;color:var(--muted);font-size:13px;text-decoration:none}"
           " .topnav a.on{background:var(--accent);color:#fff;border-color:var(--accent)}"
           " .topnav a:hover{border-color:var(--accent-2);color:var(--accent-2)}"
           " .topnav a.on:hover{color:#fff}")


def _nav(active):
    """상단 공용 내비게이션. active: 'home'|'stocks'|'people'"""
    items = [("home", "index.html", "홈"), ("stocks", "stocks.html", "종목 랭킹"),
             ("people", "people.html", "공직자 검색"), ("column", "column.html", "칼럼"),
             ("", "about.html", "소개")]
    return '<nav class="topnav">' + "".join(
        '<a href="%s"%s>%s</a>' % (href, ' class="on"' if key == active else "", label)
        for key, href, label in items) + "</nav>"


def build_site(domestic, overseas, prices=None, detail_dom=None, detail_ovs=None, timeseries=None,
               person_port=None, whales=None, asset_totals=None, switches=None,
               focus=None, ppx=None, ndocs=0):
    """3페이지 생성: index.html(메인) / stocks.html(종목 랭킹) / people.html(공직자 검색).
    반환: {"index": html, "stocks": html, "people": html}"""
    prices = prices or {}
    detail_dom = detail_dom or {}
    detail_ovs = detail_ovs or {}
    timeseries = timeseries or {}
    person_port = person_port or {}
    whales = whales or {}
    newsdata = (json.load(open(NEWS_FILE, encoding="utf-8"))
                if os.path.exists(NEWS_FILE) else
                {"stocks": {}, "people": {}, "filings": {}, "updated": None})
    asset_totals = asset_totals or {}
    switches = switches or []
    focus = focus or []
    ppx = ppx or {}

    def rows_json(rows):
        arr = []
        for k, who, sh in rows:
            row = {"n": k, "p": len(who), "s": int(sh)}
            pr = prices.get(k)
            if pr and pr.get("overseas"):
                # 해외: 달러 표기
                row["ov"] = 1
                row["u15"] = pr.get("usd15")
                row["u20"] = pr.get("usd20")
                row["u25"] = pr.get("usd25")
                row["uc"] = pr.get("usdcur")
                row["r"] = pr.get("rate")
            elif pr and pr.get("cur"):
                # 국내: 원화
                row["c"] = pr["cur"]
                row["r"] = pr.get("rate")
                row["p15"] = pr.get("p2015")
                row["p20"] = pr.get("p2020")
                row["p25"] = pr.get("p2025")
            arr.append(row)
        return json.dumps(arr, ensure_ascii=False)

    html = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>종목 랭킹 · 같이투자</title>
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9186959187584058" crossorigin="anonymous"></script>
<meta name="description" content="__DESC__">
<meta property="og:title" content="종목 랭킹 · 같이투자">
<meta property="og:description" content="__DESC__">
<meta property="og:type" content="website">
<meta property="og:locale" content="ko_KR">
<meta name="twitter:card" content="summary">
<style>
 :root{--line:#dfe3ea;--muted:#5b6472;--accent:#1f3a5f;--accent-2:#2e527f;--tint:#eef2f7;--bg:#f6f7f9;--ink:#1a2230;--up:#c0392b;--down:#1e5fbf}
 *{box-sizing:border-box} body{margin:0;font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:var(--bg);color:var(--ink)}
 .wrap{max-width:920px;margin:0 auto;padding:28px 18px 60px}
 h1{font-size:24px;font-weight:800;margin:0 0 2px;color:var(--accent);letter-spacing:-.01em}
 .tag{font-size:17px;font-weight:700;color:var(--accent-2);margin:0 0 2px}
 .tagsub{font-size:14px;font-weight:500;color:var(--ink);margin:0 0 8px;opacity:.85}
 .sub{color:var(--muted);font-size:13px;margin:0 0 16px}
 .whales{margin-top:30px;border-top:1px solid var(--line);padding-top:20px}
 .whales h2{font-size:17px;color:var(--accent);margin:0 0 4px}
 .whales .dgsub{color:var(--muted);font-size:12px;margin:0 0 14px}
 .wgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px}
 .wcard{background:#fff;border:1px solid var(--line);border-radius:10px;padding:13px 15px}
 .wcard h3{font-size:14px;color:var(--accent-2);margin:0 0 2px}
 .wcard .wsub{font-size:11px;color:var(--muted);margin:0 0 8px}
 .wcard ol{margin:0;padding-left:20px}
 .wcard li{font-size:13px;line-height:2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .wv{float:right;font-variant-numeric:tabular-nums;color:var(--accent);font-weight:600}
 .worg{color:var(--muted);font-size:11px;margin-left:4px}
 .switchbox{background:var(--tint);border:1px solid #d6e0ee;border-radius:8px;padding:11px 14px;font-size:13px;line-height:1.8;margin-top:12px}
 .switchbox b{color:var(--accent)}
 .stat{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
 .card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 16px;flex:1;min-width:130px}
 .card .lab{font-size:12px;color:var(--muted)} .card .val{font-size:22px;font-weight:700;margin-top:2px;color:var(--accent)}
 .tabs{display:flex;gap:6px;margin-bottom:12px}
 .tab{padding:7px 16px;border:1px solid var(--line);border-radius:99px;background:#fff;cursor:pointer;font-size:14px;color:var(--muted)}
 .tab.on{background:var(--accent);color:#fff;border-color:var(--accent)}
 input{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font-size:14px;margin-bottom:12px;background:#fff;color:var(--ink)}
 input:focus{outline:2px solid var(--accent-2);outline-offset:-1px;border-color:var(--accent-2)}
 .tblwrap{background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden;overflow-x:auto;-webkit-overflow-scrolling:touch}
 table{width:100%;border-collapse:collapse;background:#fff}
 th,td{text-align:left;padding:10px 12px;font-size:14px;border-top:1px solid var(--line)}
 th{background:var(--tint);color:var(--muted);font-weight:600;border-top:none;white-space:nowrap;line-height:1.35}
 td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
 td{white-space:nowrap}
 .rank{color:var(--muted);width:36px} .nm{font-weight:500}
 .tk{display:inline-block;margin-left:6px;padding:1px 6px;border:1px solid #cfd9e6;border-radius:5px;background:var(--tint);color:var(--accent-2);font-size:11px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:.02em;vertical-align:middle}
 #mod h2 .tk{font-size:13px}
 .up{color:var(--up)} .down{color:var(--down)}
 .note{color:var(--muted);font-size:12px;margin-top:10px;line-height:1.6}
 .foot{color:var(--muted);font-size:12px;margin-top:24px;border-top:1px solid var(--line);padding-top:12px;line-height:1.7}
 .foot a{color:var(--accent-2);text-decoration:none} .foot a:hover{text-decoration:underline}
 .footlinks{margin-top:6px}
 tr.row{cursor:pointer} tr.row:hover{background:#f0f3f8}
 #tip{position:fixed;z-index:20;background:#14243a;color:#fff;font-size:12px;line-height:1.5;padding:8px 10px;border-radius:8px;pointer-events:none;display:none;max-width:240px;box-shadow:0 4px 16px rgba(20,36,58,.28)}
 #tip b{color:#ffd9a0}
 #ov{position:fixed;inset:0;background:rgba(20,36,58,.45);display:none;z-index:30;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto}
 #mod{background:#fff;border-radius:14px;max-width:820px;width:100%;padding:22px 24px 26px;box-shadow:0 12px 48px rgba(20,36,58,.3)}
 #mod h2{margin:0 0 2px;font-size:20px;color:var(--accent)} #mod .msub{color:var(--muted);font-size:13px;margin-bottom:16px}
 #mod .close{float:right;cursor:pointer;color:var(--muted);font-size:22px;line-height:1;border:none;background:none}
 #mod td{white-space:normal}
 .pl{color:var(--accent-2);cursor:pointer;border-bottom:1px dotted var(--accent-2)}
 .backlink{color:var(--accent-2);font-size:13px;cursor:pointer;margin-bottom:8px;display:inline-block}
 .backlink:hover{text-decoration:underline}
 .pwrap{max-height:56vh;overflow:auto;border-top:1px solid var(--line);padding-top:12px}
 .pcols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
 .pt{font-size:13px;color:var(--accent-2);margin:0 0 6px}
 .ptab{width:100%;border-collapse:collapse}
 .ptab td{padding:7px 8px;font-size:13px;border-top:1px solid var(--line)}
 tr.prow{cursor:pointer} tr.prow:hover{background:#f0f3f8}
 .orgbar{display:flex;flex-direction:column;gap:6px;margin-bottom:18px}
 .orgrow{display:grid;grid-template-columns:130px 1fr 40px;align-items:center;gap:8px;font-size:13px}
 .bar{height:16px;background:linear-gradient(90deg,var(--accent),var(--accent-2));border-radius:4px;min-width:2px}
 .hl{max-height:min(48vh,430px);overflow:auto;border-top:1px solid var(--line)}
 .hl table{border:none;border-radius:0} .hl th{position:sticky;top:0}
 .wtbox{background:var(--tint);border:1px solid #d6e0ee;border-radius:8px;padding:11px 14px;font-size:13px;margin-bottom:16px;line-height:1.5}
 .wtbox b{color:var(--accent);font-size:16px} .wtbox .wtn{color:var(--muted);font-size:12px}
 .story{background:var(--tint);border-left:3px solid var(--accent-2);border-radius:0 8px 8px 0;padding:11px 14px;font-size:13px;line-height:1.75;margin-bottom:14px}
 .story b{color:var(--accent)}
 .lead{background:#fff;border:1px solid var(--line);border-radius:10px;padding:13px 16px;font-size:13.5px;line-height:1.8;color:var(--ink);margin:0 0 18px}
 .lead b{color:var(--accent)}
 .digest{margin-top:30px;border-top:1px solid var(--line);padding-top:20px}
 .digest h2{font-size:17px;color:var(--accent);margin:0 0 4px}
 .digest .dgsub{color:var(--muted);font-size:12px;margin:0 0 14px}
 .digest h3{font-size:14px;color:var(--accent-2);margin:18px 0 8px}
 .dg{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 15px;margin-bottom:8px}
 .dg h4{margin:0 0 5px;font-size:14px;color:var(--accent)}
 .dg p{margin:0;font-size:13px;line-height:1.75;color:var(--ink)}
 .dg .tk{margin-left:6px}
 .nb{display:inline-block;margin-left:5px;padding:0 5px;border-radius:4px;background:#e3ede1;color:#2f6b3d;font-size:10px;font-weight:700;vertical-align:middle}
 .newsbox{border-radius:10px;padding:12px 15px;margin-bottom:10px}
 .nbx-news{background:#eef6fc;border:1px solid #cfe3f4}
 .nbx-news h3{color:#0d5c8c} .nbx-news .nd{color:#7d9ab0}
 .nbx-fil{background:#fdf6e7;border:1px solid #eddfba}
 .nbx-fil h3{color:#8a5b00} .nbx-fil .nd{color:#b09a6a}
 .newsbox h3{font-size:13px;margin:0 0 7px;font-weight:700}
 .newsbox ul{margin:0;padding-left:18px} .newsbox li{font-size:13px;line-height:1.8}
 .newsbox a{color:var(--ink);text-decoration:none} .newsbox a:hover{color:var(--accent-2);text-decoration:underline}
 .newsbox .nd{color:var(--muted);font-size:11px;margin-left:6px}
 .newsbox .nsrc{color:var(--muted);font-size:11px;margin:8px 0 0}
 @media(max-width:700px){
  .wrap{padding:18px 12px 44px}
  h1{font-size:19px} .sub{font-size:13px;margin-bottom:14px}
  .stat{gap:8px;margin-bottom:14px}
  .card{padding:9px 12px;min-width:100px} .card .val{font-size:17px}
  .tab{padding:6px 13px;font-size:13px}
  th,td{padding:8px 9px;font-size:13px}
  .tblwrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  table{min-width:660px}
  #ov{padding:16px 8px}
  #mod{padding:16px 14px 20px}
  #mod h2{font-size:18px}
  .orgrow{grid-template-columns:96px 1fr 34px;font-size:12px}
  .hl{max-height:260px}
  .pcols{grid-template-columns:1fr}
 }
__NAVCSS__
</style></head><body><div class="wrap">
__NAV__
<h1>종목 랭킹</h1>
<p class="tagsub">공직자 __NPEOPLE__명이 신고한 상장주식, 종목별 순위</p>
<p class="sub">2026년 정기 재산공개(2025년말 기준) · 전자관보 원문 집계 · 등락률은 2025-12-30 종가 대비 현재가</p>
<div class="stat">
 <div class="card"><div class="lab">집계 문서</div><div class="val">__DOCS__건</div></div>
 <div class="card"><div class="lab">국내 종목</div><div class="val">__NDOM__</div></div>
 <div class="card"><div class="lab">해외·기타 종목</div><div class="val">__NOVS__</div></div>
</div>
<div class="tabs"><div class="tab on" data-t="dom">국내주식</div><div class="tab" data-t="ovs">해외·기타</div></div>
<input id="q" placeholder="종목명·티커 검색">
<div class="tblwrap"><table><thead id="th"></thead><tbody id="tb"></tbody></table></div>
<p class="note" id="note"></p>
<div class="foot">데이터 출처: 대한민국 전자관보(gwanbo.go.kr) 공직자 재산공개 원문 · 시세: 네이버 금융·야후 파이낸스. 해외주식은 티커(예: NVDA) 기준으로 표기를 통합했으며 종목명 옆에 티커를 함께 표기합니다. 티커로도 검색할 수 있습니다. 표기 정규화는 상위·빈출 종목 위주로 계속 보강 중입니다.
<div class="footlinks"><a href="about.html">소개</a> · <a href="privacy.html">개인정보처리방침</a> · <a href="contact.html">문의</a></div></div>
</div>
<div id="tip"></div>
<div id="ov"><div id="mod"></div></div>
<script>
const DOM=__DOM__, OVS=__OVS__;
const DDOM=__DDOM__, DOVS=__DOVS__;
const TS=__TS__;
const TK=__TK__;   // 해외종목 티커 {종목명: 티커}
const PP=__PP__;   // 인물 포트폴리오 {성명:{o,j,sec,d:[[종목,주수]],v:[[종목,주수]]}}
const NEWS=__NEWS__; // {stocks:{종목:[{t,l,d}]}, filings:{종목:[...]}, updated}
function newsBox(nm){
 const nw=(NEWS.stocks&&NEWS.stocks[nm])||[], fl=(NEWS.filings&&NEWS.filings[nm])||[];
 const li=a=>a.map(n=>`<li><a href="${n.l}" target="_blank" rel="noopener">${n.t}</a><span class="nd">${n.d}</span></li>`).join('');
 let out='';
 if(nw.length) out+=`<div class="newsbox nbx-news"><h3>관련 뉴스</h3><ul>${li(nw)}</ul><p class="nsrc">네이버 뉴스 검색 · 제목과 링크만 표시 · ${NEWS.updated||''} 수집</p></div>`;
 if(fl.length) out+=`<div class="newsbox nbx-fil"><h3>최근 공시</h3><ul>${li(fl)}</ul><p class="nsrc">금융감독원 전자공시(DART)</p></div>`;
 return out;
}
function tkTag(nm){ return TK[nm]?` <span class="tk">${TK[nm]}</span>`:''; }
// ── 종목 해설: 파이썬에서 생성한 단일 소스(NARR)를 모달·정적섹션이 공용 ──
const NARR=__NARR__;
function yy(y){return String(y).slice(2)+'년';}
function miniTrend(nm){
 const s=TS[nm]; if(!s||s.length<2) return '';
 const a=s[s.length-2], b=s[s.length-1], df=b[1]-a[1];
 const mark=df>0?`▲${df}`:(df<0?`▼${-df}`:'변동 없음');
 return `${yy(a[0])} ${a[1]}명 → ${yy(b[0])} ${b[1]}명 (${mark})`;
}
function narrativeOf(nm){ return NARR[nm]||''; }
const tb=document.getElementById('tb'), q=document.getElementById('q'), note=document.getElementById('note');
let cur='dom';
function fmtRate(r){
 if(r===undefined||r===null) return '<td class="num">-</td>';
 const cls=r>0?'up':(r<0?'down':'');
 const sign=r>0?'+':'';
 return `<td class="num ${cls}">${sign}${r}%</td>`;
}
function won(v){ return (v===undefined||v===null)?'-':v.toLocaleString(); }
function usd(v){ return (v===undefined||v===null)?'-':'$'+v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}); }
const th=document.getElementById('th');
const HEAD_DOM='<tr><th class="rank">#</th><th>종목</th><th class="num">보유 공직자</th><th class="num">15년말</th><th class="num">20년말</th><th class="num">25년말</th><th class="num">현재가</th><th class="num">등락률<br>(25년말 대비)</th></tr>';
const HEAD_OVS='<tr><th class="rank">#</th><th>종목</th><th class="num">보유 공직자</th><th class="num">15년말($)</th><th class="num">20년말($)</th><th class="num">25년말($)</th><th class="num">현재가($)</th><th class="num">등락률<br>(25년말 대비)</th></tr>';
function render(){
 const dom=(cur==='dom');
 const data=(dom?DOM:OVS);
 const kw=q.value.trim();
 const rows=data.filter(r=>!kw||r.n.includes(kw)||(TK[r.n]&&TK[r.n].toUpperCase().includes(kw.toUpperCase())));
 th.innerHTML = dom?HEAD_DOM:HEAD_OVS;
 tb.innerHTML=rows.slice(0,100).map((r,i)=>{
  if(dom){
   return `<tr class="row" data-nm="${r.n}"><td class="rank">${i+1}</td><td class="nm">${r.n}</td><td class="num">${r.p.toLocaleString()}명</td><td class="num">${won(r.p15)}</td><td class="num">${won(r.p20)}</td><td class="num">${won(r.p25)}</td><td class="num">${r.c?r.c.toLocaleString():'-'}</td>${fmtRate(r.r)}</tr>`;
  }
  // 해외: 달러 표시 + 티커 태그
  return `<tr class="row" data-nm="${r.n}"><td class="rank">${i+1}</td><td class="nm">${r.n}${tkTag(r.n)}</td><td class="num">${r.p.toLocaleString()}명</td><td class="num">${usd(r.u15)}</td><td class="num">${usd(r.u20)}</td><td class="num">${usd(r.u25)}</td><td class="num">${usd(r.uc)}</td>${fmtRate(r.r)}</tr>`;
 }).join('');
 note.textContent = dom ? `국내 상장종목 ${data.length}개 집계, 순위표는 상위 100위까지 표시. 종가는 각 연도 말일 기준이며 액면분할·병합이 반영된 수정주가(원화)입니다. 등락률은 25년말 대비 현재가.` : `해외·기타 ${data.length}개 집계, 순위표는 상위 100위까지 표시. 종가는 달러(USD) 표기, 등락률은 25년말 대비 현재가. 주요 종목 위주로 시세를 연동했습니다.`;
}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
 document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
 t.classList.add('on'); cur=t.dataset.t; render();
});
// hover 미리보기: 최다 보유 소속 Top3
const tip=document.getElementById('tip');
function detailOf(nm){ return (cur==='dom'?DDOM:DOVS)[nm]; }
tb.addEventListener('mousemove', e=>{
 const tr=e.target.closest('tr.row'); 
 if(!tr){ tip.style.display='none'; return; }
 const d=detailOf(tr.dataset.nm);
 if(!d){ tip.style.display='none'; return; }
 const top=d.orgs.slice(0,3).map(o=>`<b>${o[0]}</b> ${o[1]}명`).join('<br>');
 const mt=miniTrend(tr.dataset.nm);
 const fl=[]; if(d.nnew)fl.push('신규 '+d.nnew); if(d.nout)fl.push('매도 '+d.nout);
 tip.innerHTML=(mt?`<b>보유 추이</b> ${mt}<br>`:'')+(fl.length?`<b>올해 변동</b> ${fl.join(' · ')}<br>`:'')+`최다 보유 소속<br>${top}` + (d.orgs.length>3?'<br>…':'') + '<br><span style="opacity:.6">클릭하면 해설·전체 명단</span>';
 tip.style.display='block';
 tip.style.left=Math.min(e.clientX+14, innerWidth-250)+'px';
 tip.style.top=(e.clientY+14)+'px';
});
tb.addEventListener('mouseleave',()=>tip.style.display='none');
// 클릭 상세 모달 (종목 상세 ↔ 인물 포트폴리오 상호 이동)
const ov=document.getElementById('ov'), mod=document.getElementById('mod');
function openStock(nm){
 const d=DDOM[nm]||DOVS[nm]; if(!d) return;
 const maxc=d.orgs.length?d.orgs[0][1]:1;
 const bars=d.orgs.slice(0,8).map(o=>`<div class="orgrow"><span>${o[0]}</span><span class="bar" style="width:${Math.round(o[1]/maxc*100)}%"></span><span class="num">${o[1]}</span></div>`).join('');
 const rows=d.holders.map(h=>`<tr><td>${h.o}</td><td>${h.j}</td><td><span class="pl" data-p="${h.n}" data-from="${nm}">${h.n}</span>${h.new?' <span class="nb">신규</span>':''}</td><td class="num">${h.q.toLocaleString()}</td></tr>`).join('');
 const story=narrativeOf(nm);
 mod.innerHTML=`<button class="close" onclick="closeMod()">×</button>
  <h2>${nm}${tkTag(nm)}</h2><div class="msub">보유 공직자 ${d.holders.length}명 · 소속 기관별 분포 · 명단의 이름을 누르면 개인 포트폴리오</div>
  ${story?`<div class="story">${story}</div>`:''}
  ${newsBox(nm)}
  ${d.wt!==undefined?`<div class="wtbox">이 종목 보유자는 평균적으로 증권 자산의 <b>${d.wt}%</b>를 ${nm}에 담았습니다 <span class="wtn">(${d.wtn}명 기준)</span></div>`:''}
  ${chartHTML(nm)}
  <div class="orgbar">${bars}</div>
  <div class="hl"><table><thead><tr><th>소속</th><th>직위</th><th>성명</th><th class="num">보유주수</th></tr></thead><tbody>${rows}</tbody></table></div>`;
 ov.style.display='flex'; tip.style.display='none'; mod.scrollIntoView({block:'start'});
}
function fmtKrw(chun){ // 천원 → 억/만원
 if(chun==null) return null;
 const man=chun/10;
 if(man>=10000){const v=man/10000; return (v>=100?Math.round(v).toLocaleString():v.toFixed(1).replace(/\.0$/,''))+'억원';}
 return Math.round(man).toLocaleString()+'만원';
}
function openPerson(p, from){
 const d=PP[p]; if(!d) return;
 const sec=fmtKrw(d.sec);
 const li=(arr,ovz)=>arr.length?arr.map(x=>`<tr class="prow" data-nm="${x[0]}"><td>${x[0]}${ovz?tkTag(x[0]):''}</td><td class="num">${x[1].toLocaleString()}주</td></tr>`).join(''):'<tr><td colspan="2" style="color:var(--muted)">신고 내역 없음</td></tr>';
 mod.innerHTML=`<button class="close" onclick="closeMod()">×</button>
  ${from?`<div class="backlink" data-nm="${from}">← ${from} 상세로 돌아가기</div>`:''}
  <h2>${p}</h2><div class="msub">${d.o} · ${d.j}${sec?` · 증권 신고액 약 ${sec}`:''}</div>
  <div class="pwrap"><div class="pcols">
   <div><h3 class="pt">국내주식 ${d.d.length}종목</h3><table class="ptab"><tbody>${li(d.d,false)}</tbody></table></div>
   <div><h3 class="pt">해외·기타 ${d.v.length}종목</h3><table class="ptab"><tbody>${li(d.v,true)}</tbody></table></div>
  </div></div>
  <div class="note">2025년말 기준 상장주식 신고분(가족 명의 포함) · 종목을 누르면 종목 상세로 이동합니다 · 동명이인은 구분되지 않을 수 있습니다.</div>`;
 ov.style.display='flex'; tip.style.display='none';
}
tb.addEventListener('click', e=>{
 const tr=e.target.closest('tr.row'); if(!tr) return;
 openStock(tr.dataset.nm);
});
mod.addEventListener('click', e=>{
 const pl=e.target.closest('.pl'); if(pl){ openPerson(pl.dataset.p, pl.dataset.from); return; }
 const bk=e.target.closest('.backlink'); if(bk){ openStock(bk.dataset.nm); return; }
 const pr=e.target.closest('tr.prow'); if(pr&&(DDOM[pr.dataset.nm]||DOVS[pr.dataset.nm])){ openStock(pr.dataset.nm); return; }
});
// 큰손 랭킹의 이름 클릭 → 인물 포트폴리오(주식 신고분이 있는 인물만)
const wh=document.querySelector('.whales');
if(wh) wh.addEventListener('click', e=>{ const pl=e.target.closest('.pl'); if(pl) openPerson(pl.dataset.p); });
function chartHTML(nm){
 const s=TS[nm];
 if(!s||s.length<2) return '';
 const W=500,H=160,padX=30,padTop=28,padBot=26;
 const ys=s.map(p=>p[1]), maxY=Math.max(...ys), minY=0;
 const x=i=>padX+(W-2*padX)*(s.length<2?0:i/(s.length-1));
 const y=v=>padTop+(H-padTop-padBot)*(maxY===minY?0.5:1-(v-minY)/(maxY-minY));
 const pts=s.map((p,i)=>`${x(i)},${y(p[1])}`).join(' ');
 // 은은한 가로 그리드 + 기준선
 const grid=[0.5,1].map(t=>{const gy=padTop+(H-padTop-padBot)*t;
  return `<line x1="${padX}" y1="${gy}" x2="${W-padX}" y2="${gy}" stroke="#e7ebf1" stroke-width="1"/>`;}).join('');
 // 라인 아래 영역 채움(네이비 틴트)
 const area=`<polygon points="${pts} ${x(s.length-1)},${y(0)} ${x(0)},${y(0)}" fill="rgba(31,58,95,.08)"/>`;
 const dots=s.map((p,i)=>`<circle cx="${x(i)}" cy="${y(p[1])}" r="3.2" fill="#fff" stroke="var(--accent-2)" stroke-width="2"/><text x="${x(i)}" y="${y(p[1])-9}" font-size="10" font-weight="600" text-anchor="middle" fill="var(--accent)">${p[1]}</text>`).join('');
 const labels=s.map((p,i)=>`<text x="${x(i)}" y="${H-8}" font-size="10" text-anchor="middle" fill="var(--muted)">${String(p[0]).slice(2)}년</text>`).join('');
 return `<div style="margin-bottom:16px"><div style="font-size:12px;color:var(--muted);margin-bottom:4px">연도별 보유 공직자 수 (기준연도)</div>
  <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;background:#fff;border:1px solid var(--line);border-radius:8px">
   ${grid}${area}<polyline points="${pts}" fill="none" stroke="var(--accent-2)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>${dots}${labels}
  </svg></div>`;
}
function closeMod(){ ov.style.display='none'; }
ov.addEventListener('click',e=>{ if(e.target===ov) closeMod(); });
q.oninput=render; render();
// 딥링크: stocks.html#s=종목명 → 해당 종목 모달 자동 오픈 (해외 종목이면 탭 전환)
(function(){
 const m=location.hash.match(/^#s=(.+)$/);
 if(!m) return;
 const nm=decodeURIComponent(m[1]);
 if(DOVS[nm]&&!DDOM[nm]){ document.querySelector('.tab[data-t="ovs"]').click(); }
 if(DDOM[nm]||DOVS[nm]) openStock(nm);
})();
</script></body></html>"""
    # 해외종목 티커 맵 {종목명: 티커} — 웹 표기·검색용. (ADR 등 괄호주석은 떼고도 조회)
    from holdings_parser import OVERSEAS_TICKER

    def ticker_of(k):
        return OVERSEAS_TICKER.get(k) or OVERSEAS_TICKER.get(re.sub(r"\([^()]*\)$", "", k))

    tk_map = {k: ticker_of(k) for k, who, sh in overseas if ticker_of(k)}

    # ── 종목 해설(단일 소스): 파이썬에서 생성 → 모달(JS)·검색용 정적 섹션 공용 ──
    def _josa(w, a, b):
        if not w:
            return a
        c = ord(w[-1])
        if 0xAC00 <= c <= 0xD7A3:
            return a if (c - 0xAC00) % 28 else b
        return "%s(%s)" % (a, b)         # 비한글(티커 등)은 '은(는)'

    def _yy(y):
        return str(y)[2:] + "년"

    def _trend(series):
        if not series or len(series) < 2:
            return ""
        a, b = series[-2], series[-1]; df = b[1] - a[1]
        inc = dec = 0
        for i in range(len(series) - 1, 0, -1):
            d2 = series[i][1] - series[i - 1][1]
            if d2 > 0 and dec == 0:
                inc += 1
            elif d2 < 0 and inc == 0:
                dec += 1
            else:
                break
        t = "보유 공직자는 %s %s명에서 %s %s명으로 " % (_yy(a[0]), format(a[1], ","), _yy(b[0]), format(b[1], ","))
        if inc >= 2:
            t += "늘어 %s 이후 %d년 연속 증가세다." % (_yy(series[-1 - inc][0]), inc)
        elif dec >= 2:
            t += "줄어 %s 이후 %d년 연속 감소세다." % (_yy(series[-1 - dec][0]), dec)
        elif df > 0:
            t += "%s명 늘었다." % format(df, ",")
        elif df < 0:
            t += "%s명 줄었다." % format(-df, ",")
        else:
            t += "변화가 없다."
        return t

    def narrative(k, tab_label, rank, people, tot, entry, series, pr, overseas):
        entry = entry or {}
        ps = ["<b>%s</b>%s 2025년말 기준 고위공직자 <b>%s명</b>이 보유를 신고한 %s %d위 종목이다."
              % (k, _josa(k, "은", "는"), format(people, ","), tab_label, rank)]
        if tot:
            ps.append("신고된 보유량은 모두 %s주다." % format(int(tot), ","))
        ts = _trend(series)
        if ts:
            ps.append(ts)
        nnew, nout = entry.get("nnew"), entry.get("nout")
        if nnew or nout:
            fl = []
            if nnew:
                fl.append("새로 사들인 공직자 <b>%d명</b>" % nnew)
            if nout:
                fl.append("전량 매도한 공직자 <b>%d명</b>" % nout)
            ps.append("이번 공개에서 지난해 대비 %s이 확인된다." % ", ".join(fl))
        orgs = entry.get("orgs") or []
        if orgs:
            t = "소속별로는 %s(%d명)" % (orgs[0][0], orgs[0][1])
            if len(orgs) > 1:
                t += ", %s(%d명)" % (orgs[1][0], orgs[1][1])
            t += " 순으로 많다."
            share = round(sum(c for _, c in orgs[:3]) / people * 100) if people else 0
            if len(orgs) >= 3 and share >= 55:
                t += " 상위 3개 기관이 보유자의 %d%%를 차지할 만큼 쏠려 있다." % share
            ps.append(t)
        if pr and pr.get("rate") is not None:
            r = pr["rate"]
            if (not overseas) and pr.get("p2025") and pr.get("cur"):
                ps.append("주가는 25년말 %s원에서 현재 %s원으로 %s%s%% %s했다."
                          % (format(pr["p2025"], ","), format(pr["cur"], ","), "+" if r > 0 else "", r, "상승" if r >= 0 else "하락"))
            elif overseas and pr.get("usd25") and pr.get("usdcur"):
                ps.append("주가는 25년말 $%s에서 현재 $%s로 %s%s%% %s했다."
                          % (format(pr["usd25"], ",.2f"), format(pr["usdcur"], ",.2f"), "+" if r > 0 else "", r, "상승" if r >= 0 else "하락"))
        return " ".join(ps)

    narr = {}
    for i, (k, who, tot) in enumerate(domestic):
        if k in detail_dom:
            narr[k] = narrative(k, "국내", i + 1, len(who), tot, detail_dom.get(k), timeseries.get(k), prices.get(k), False)
    for i, (k, who, tot) in enumerate(overseas):
        if k in detail_ovs:
            narr[k] = narrative(k, "해외·기타", i + 1, len(who), tot, detail_ovs.get(k), timeseries.get(k), prices.get(k), True)

    # 검색용 정적 해설 섹션(상위 12종목/탭) — JS 없이도 크롤링·읽기 가능
    dg = ['<section class="digest" id="digest"><h2>주요 종목 해설</h2>',
          '<p class="dgsub">아래 해설은 위 표의 자체 집계 데이터로 자동 작성됩니다. 전체 순위·검색은 위 표에서 확인하세요.</p>']
    for label, rows, isov in [("국내주식", domestic, False), ("해외·기타", overseas, True)]:
        dg.append('<h3>%s</h3>' % label)
        for k, who, tot in rows[:12]:
            if k in narr:
                from urllib.parse import quote as _q
                tk = ticker_of(k) if isov else None
                tg = ' <span class="tk">%s</span>' % tk if tk else ''
                dg.append('<div class="dg"><h4><a href="stocks.html#s=%s">%s</a>%s</h4><p>%s</p></div>'
                          % (_q(k), k, tg, narr[k]))
    dg.append('</section>')
    digest_html = "".join(dg)

    # ── 큰손 랭킹 섹션 + 자산 합계 카드 + 갈아타기 박스 (파이썬에서 정적 생성 → SEO 노출) ──
    def fmt_krw(chun):
        """천원 → '8.2조원'/'1,257억원'/'3,400만원'"""
        if not chun:
            return "-"
        man = chun / 10.0                      # 만원
        if man >= 1e8:                          # 1조 = 1e8만원
            v = man / 1e8
            return ("%.1f" % v).rstrip("0").rstrip(".") + "조원"
        if man >= 1e4:                          # 1억 = 1e4만원
            v = man / 1e4
            return (format(round(v), ",") if v >= 100 else ("%.1f" % v).rstrip("0").rstrip(".")) + "억원"
        return format(round(man), ",") + "만원"

    from urllib.parse import quote

    # 큰손 카드 — 순위칩+그리드 행, 카테고리별 색상, 1–5/6–10위 패널 로테이션(부드러운 등장 애니메이션)
    WCOLOR = {"stock": ("#1f3a5f", "#eef2f7"), "estate": ("#166534", "#ecf7ef"),
              "coin": ("#92400e", "#fdf3e3"), "cash": ("#0e7490", "#e7f6f9"),
              "gain": ("#b91c1c", "#fdeeee"), "focus": ("#6b21a8", "#f5eefc")}

    def _wli(rank_no, r, val_html):
        nm_html = '<a class="pl" href="people.html?p=%s">%s</a>' % (quote(r["n"]), r["n"])
        return ('<div class="wli"><span class="rk">%d</span>'
                '<span class="wn">%s<span class="worg">%s</span></span>'
                '<span class="wv">%s</span></div>' % (rank_no, nm_html, r["o"][:12], val_html))

    def _wpanels(rows, valfn):
        panels = ['<div class="rp on">%s</div>'
                  % "".join(_wli(i + 1, r, valfn(r)) for i, r in enumerate(rows[:5]))]
        if len(rows) > 5:
            panels.append('<div class="rp">%s</div>'
                          % "".join(_wli(i + 6, r, valfn(r)) for i, r in enumerate(rows[5:10])))
        return "".join(panels)

    def _wcard(key, title, subtitle, panels_html):
        c, bg = WCOLOR.get(key, ("#1f3a5f", "#eef2f7"))
        return ('<div class="wcard" data-rot style="--wc:%s;--wbg:%s">'
                '<div class="whead"><h3>%s</h3><p class="wsub">%s</p></div>%s</div>'
                % (c, bg, title, subtitle, panels_html))

    wcards = []
    for key, (title, subtitle, rows) in whales.items():
        if not rows:
            continue
        wcards.append(_wcard(key, title, subtitle, _wpanels(rows, lambda r: fmt_krw(r["v"]))))
    if focus:
        wcards.append(_wcard("focus", "최다 종목 보유", "국내+해외 상장주식 보유 종목 수",
                             _wpanels(focus, lambda r: "%d종목" % r["w"])))
    switch_html = ""
    if switches:
        parts = ["<b>%s</b>를 정리하고 <b>%s</b>를 새로 담은 공직자 <b>%d명</b>" % (sk, bk, c)
                 for c, sk, bk in switches]
        switch_html = ('<div class="switchbox" id="switch">이번 공개의 갈아타기 흐름 — %s이 확인됩니다.</div>'
                       % ", ".join(parts))

    # 홈 상단 종목 랭킹: 국내 1–5 → 국내 6–10 → 해외 1–5 → 해외 6–10 로테이션
    def _rank_rows(rows, start, isov):
        out = []
        for i, (k, who, tot) in enumerate(rows):
            tk = ticker_of(k) if isov else None
            tg = ' <span class="tk">%s</span>' % tk if tk else ''
            out.append('<div class="wli"><span class="rk">%d</span>'
                       '<span class="wn"><a class="pl" href="stocks.html#s=%s">%s</a>%s</span>'
                       '<span class="wv">%s명</span></div>'
                       % (start + i, quote(k), k, tg, format(len(who), ",")))
        return "".join(out)

    rank_html = ('<section class="rank" id="rank"><h2>종목 랭킹</h2>'
                 '<p class="dgsub">보유 공직자 수 기준 · <a href="stocks.html">전체 순위 보기 →</a></p>'
                 '<div class="rgrid">'
                 '<div class="wcard rankcard" data-rot style="--wc:#1f3a5f;--wbg:#eef2f7">'
                 '<div class="rp on"><div class="whead"><h3>국내주식 1–5위</h3></div>%s</div>'
                 '<div class="rp"><div class="whead"><h3>국내주식 6–10위</h3></div>%s</div></div>'
                 '<div class="wcard rankcard" data-rot style="--wc:#0e7490;--wbg:#e7f6f9">'
                 '<div class="rp on"><div class="whead"><h3>해외주식 1–5위</h3></div>%s</div>'
                 '<div class="rp"><div class="whead"><h3>해외주식 6–10위</h3></div>%s</div></div>'
                 '</div></section>'
                 % (_rank_rows(domestic[:5], 1, False), _rank_rows(domestic[5:10], 6, False),
                    _rank_rows(overseas[:5], 1, True), _rank_rows(overseas[5:10], 6, True)))
    whales_html = ""
    if wcards:
        whales_html = ('<section class="whales" id="whales"><h2>큰손 랭킹</h2>'
                       '<p class="dgsub">2025년말 신고가액 기준(공직자윤리법상 신고액이며 시가와 다를 수 있음) · '
                       '이름을 누르면 공직자 상세</p>'
                       '<div class="wgrid">%s</div>%s</section>' % ("".join(wcards), switch_html))

    # 리드 요약(가시적) + 메타 설명(SEO/공유)
    td = [k for k, _, _ in domestic[:3]]
    tov = [k for k, _, _ in overseas[:3]]
    nb_rank = sorted(
        [(e.get("nnew", 0), k) for d in (detail_dom, detail_ovs) for k, e in d.items()],
        reverse=True)
    tnew = [k for c, k in nb_rank[:3] if c]
    lead = ("2026년 정기 재산공개(2025년말 기준)에서 고위공직자가 가장 많이 보유한 종목은 "
            "국내는 <b>%s</b>, 해외는 <b>%s</b> 순입니다. 이번 공개에서 새로 사들인 공직자가 많았던 종목은 "
            "<b>%s</b>였습니다. 종목별 보유 공직자 수·연도별 추세·신규 매수/전량 매도·시세를 함께 정리했습니다."
            % (" · ".join(td), " · ".join(tov), " · ".join(tnew)))
    desc = re.sub(r"<[^>]+>", "", lead).replace('"', "'")
    stocks_desc = ("고위공직자가 보유한 상장주식 종목별 순위 — 보유 공직자 수, 연도별 추세, "
                   "신규 매수·전량 매도, 시세를 한눈에.")

    # ── ① 종목 랭킹 페이지 (stocks.html) ──
    stocks_html = (html.replace("__DOM__", rows_json(domestic))
                   .replace("__OVS__", rows_json(overseas))
                   .replace("__DDOM__", json.dumps(detail_dom, ensure_ascii=False))
                   .replace("__DOVS__", json.dumps(detail_ovs, ensure_ascii=False))
                   .replace("__TS__", json.dumps(timeseries, ensure_ascii=False))
                   .replace("__TK__", json.dumps(tk_map, ensure_ascii=False))
                   .replace("__NARR__", json.dumps(narr, ensure_ascii=False))
                   .replace("__PP__", json.dumps(person_port, ensure_ascii=False))
                   .replace("__NEWS__", json.dumps({"stocks": newsdata.get("stocks", {}),
                                                    "filings": newsdata.get("filings", {}),
                                                    "updated": newsdata.get("updated")}, ensure_ascii=False))
                   .replace("__NAVCSS__", NAV_CSS)
                   .replace("__NAV__", _nav("stocks"))
                   .replace("__DESC__", stocks_desc)
                   .replace("__NPEOPLE__", format(len(person_port), ","))
                   .replace("__DOCS__", str(ndocs))
                   .replace("__NDOM__", str(len(domestic)))
                   .replace("__NOVS__", str(len(overseas))))

    # ── ② 메인 페이지 (index.html) — 전부 정적, SEO 최적 ──
    base_css = """
 :root{--line:#dfe3ea;--muted:#5b6472;--accent:#1f3a5f;--accent-2:#2e527f;--tint:#eef2f7;--bg:#f6f7f9;--ink:#1a2230;--up:#c0392b;--down:#1e5fbf}
 *{box-sizing:border-box} body{margin:0;font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:var(--bg);color:var(--ink);font-size:15px}
 .wrap{max-width:960px;margin:0 auto;padding:30px 20px 64px}
 h1{font-size:30px;font-weight:800;margin:0 0 4px;color:var(--accent);letter-spacing:-.01em}
 .tag{font-size:20px;font-weight:700;color:var(--accent-2);margin:0 0 3px}
 .tagsub{font-size:15px;font-weight:500;color:var(--ink);margin:0 0 10px;opacity:.85}
 .sub{color:var(--muted);font-size:13.5px;margin:0 0 18px;line-height:1.7}
 html{scroll-behavior:smooth}
 .stat{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
 .searchlabel{font-size:15px;color:var(--ink);margin:0 0 8px} .searchlabel b{color:var(--accent)}
 .notice{font-size:12.5px;color:var(--muted);margin:8px 0 20px} .notice a{color:var(--accent-2)}
 .slogan{font-size:15px;font-weight:600;color:var(--muted);margin-left:8px;letter-spacing:0}
 .psearch{display:flex;gap:8px;margin:0}
 .psearch input{flex:1;padding:13px 18px;border:1.5px solid var(--line);border-radius:12px;font-size:15px;background:#fff;color:var(--ink)}
 .psearch input:focus{outline:none;border-color:var(--accent-2);box-shadow:0 0 0 3px rgba(46,82,127,.12)}
 .psearch button{padding:0 26px;border:none;border-radius:12px;background:var(--accent);color:#fff;font-size:15px;font-weight:700;cursor:pointer}
 .psearch button:hover{background:var(--accent-2)}
 .rgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
 .secmenu{display:flex;gap:8px;flex-wrap:wrap;margin:22px 0 16px}
 .secmenu a{padding:9px 18px;border-radius:10px;background:#fff;border:1px solid var(--line);color:var(--accent-2);font-weight:700;font-size:14px;text-decoration:none}
 .secmenu a:hover{border-color:var(--accent-2);color:var(--accent)}
 .card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 18px;flex:1;min-width:140px;border-top:3px solid var(--accent)}
 .stat .card:nth-child(2){border-top-color:#2e527f}
 .stat .card:nth-child(3){border-top-color:#166534}
 .stat .card:nth-child(4){border-top-color:#92400e}
 .card .lab{font-size:13px;color:var(--muted)} .card .val{font-size:26px;font-weight:800;margin-top:3px;color:var(--accent)}
 .lead{background:#fff;border:1px solid var(--line);border-radius:12px;padding:15px 18px;font-size:14.5px;line-height:1.85;margin:0 0 22px}
 .lead b{color:var(--accent)}
 section h2{font-size:20px;color:var(--accent);margin:0 0 4px;position:relative;padding-left:13px}
 section h2::before{content:'';position:absolute;left:0;top:5px;bottom:5px;width:5px;border-radius:3px;background:var(--accent-2)}
 .rank{margin-top:8px}
 .dgsub{color:var(--muted);font-size:13px;margin:0 0 14px;padding-left:13px}
 .dgsub a{color:var(--accent-2);text-decoration:none} .dgsub a:hover{text-decoration:underline}
 .rankcard{margin-bottom:22px}
 .whales{margin-top:30px;border-top:1px solid var(--line);padding-top:22px}
 .wgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
 .wcard{background:#fff;border:1px solid var(--line);border-radius:12px;padding:0 16px 10px;overflow:hidden}
 .whead{background:var(--wbg,var(--tint));margin:0 -16px 6px;padding:12px 16px 10px}
 .wcard h3{font-size:15.5px;font-weight:700;color:var(--wc,var(--accent));margin:0}
 .wcard .wsub{font-size:11.5px;color:var(--muted);margin:3px 0 0}
 .wli{display:grid;grid-template-columns:26px minmax(0,1fr) auto;gap:10px;align-items:center;padding:8px 0;border-top:1px solid #f1f3f7;font-size:14px}
 .rp .wli:first-of-type{border-top:none}
 .rk{width:25px;height:25px;border-radius:8px;background:var(--wbg,var(--tint));color:var(--wc,var(--accent));font-weight:800;font-size:13px;display:flex;align-items:center;justify-content:center}
 .wn{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;line-height:1.4}
 .wn a{font-weight:700}
 .worg{display:block;font-size:11.5px;color:var(--muted);margin-top:1px}
 .wv{font-variant-numeric:tabular-nums;color:var(--wc,var(--accent));font-weight:800;font-size:14.5px;white-space:nowrap}
 .pl{color:var(--accent-2);text-decoration:none;border-bottom:1px dotted #b9c6d8}
 .pl:hover{color:var(--accent);border-bottom-style:solid}
 .rp{display:none} .rp.on{display:block}
 .rp.on .whead{animation:rin .45s cubic-bezier(.22,.7,.3,1) both}
 .rp.on .wli{opacity:0;animation:rin .5s cubic-bezier(.22,.7,.3,1) forwards}
 .rp.on .wli:nth-of-type(1){animation-delay:.04s}
 .rp.on .wli:nth-of-type(2){animation-delay:.1s}
 .rp.on .wli:nth-of-type(3){animation-delay:.16s}
 .rp.on .wli:nth-of-type(4){animation-delay:.22s}
 .rp.on .wli:nth-of-type(5){animation-delay:.28s}
 @keyframes rin{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
 @media(prefers-reduced-motion:reduce){.rp.on .wli,.rp.on .whead{animation:none;opacity:1}}
 .switchbox{background:var(--tint);border:1px solid #d6e0ee;border-radius:10px;padding:13px 16px;font-size:14px;line-height:1.85;margin-top:14px}
 .switchbox b{color:var(--accent)}
 .digest{margin-top:30px;border-top:1px solid var(--line);padding-top:22px}
 .digest h3{font-size:15px;color:var(--accent-2);margin:20px 0 10px;padding-left:13px}
 .dg{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 17px;margin-bottom:9px}
 .dg h4{margin:0 0 6px;font-size:15.5px;color:var(--accent)} .dg h4 a{color:inherit;text-decoration:none} .dg h4 a:hover{text-decoration:underline}
 .dg p{margin:0;font-size:14px;line-height:1.8}
 .tk{display:inline-block;margin-left:6px;padding:1px 7px;border:1px solid #cfd9e6;border-radius:5px;background:var(--tint);color:var(--accent-2);font-size:11.5px;font-weight:700;vertical-align:middle}
 .foot{color:var(--muted);font-size:12.5px;margin-top:28px;border-top:1px solid var(--line);padding-top:14px;line-height:1.8}
 .foot a{color:var(--accent-2);text-decoration:none} .foot a:hover{text-decoration:underline}
 @media(max-width:700px){.wrap{padding:20px 13px 48px} h1{font-size:24px} .tag{font-size:17px} .card{padding:10px 13px;min-width:105px} .card .val{font-size:20px} .wgrid{grid-template-columns:1fr} .rgrid{grid-template-columns:1fr}}
""" + NAV_CSS

    foot_html = ('<div class="foot">데이터 출처: 대한민국 전자관보(gwanbo.go.kr) 공직자 재산공개 원문 · '
                 '시세: 네이버 금융·야후 파이낸스 · 금액은 공직자윤리법상 신고가액'
                 '<div class="footlinks"><a href="about.html">소개</a> · <a href="privacy.html">개인정보처리방침</a> · '
                 '<a href="contact.html">문의</a></div></div>')

    index_html = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>같이투자 : 가치있는 투자를 다같이</title>
<meta name="naver-site-verification" content="b418f5eb05d891a0eab29fdafa22d399f86d97b6" />
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9186959187584058" crossorigin="anonymous"></script>
<meta name="description" content="%(desc)s">
<meta property="og:title" content="같이투자 : 가치있는 투자를 다같이">
<meta property="og:description" content="%(desc)s">
<meta property="og:type" content="website"><meta property="og:locale" content="ko_KR">
<meta name="twitter:card" content="summary">
<style>%(css)s</style></head><body><div class="wrap">
%(nav)s
<h1>같이투자 <span class="slogan">가치있는 투자를 다같이</span></h1>
<p class="tag">공직자는 어디에 투자했을까?</p>
<p class="tagsub">공직자의 선택, 데이터로 읽다</p>
<p class="sub">2026년 정기 재산공개(2025년말 기준) · 전자관보 %(ndocs)s건에서 공직자 %(npeople)s명 집계 · 정부·국회·대법원·선관위 관할 포함(헌법재판소는 헌재공보에 별도 공개되어 미포함)</p>
<div class="stat">
 <div class="card"><div class="lab">신고 재산 총액</div><div class="val">%(a_all)s</div></div>
 <div class="card"><div class="lab">증권</div><div class="val">%(a_sec)s</div></div>
 <div class="card"><div class="lab">부동산(토지+건물)</div><div class="val">%(a_est)s</div></div>
 <div class="card"><div class="lab">가상자산</div><div class="val">%(a_coin)s</div></div>
</div>
<p class="searchlabel">공직자 <b>%(nsearch)s명</b>의 재산을 검색할 수 있습니다</p>
<form class="psearch" action="people.html" method="get">
 <input type="search" name="q" placeholder="공직자 이름 또는 소속으로 검색 (예: 안철수, 기획재정부)">
 <button type="submit">검색</button>
</form>
<p class="notice">원문 PDF 자동 추출 특성상 일부 정보가 실제와 다를 수 있습니다. 오류를 발견하시면 <a href="contact.html">제보</a>해 주세요.</p>
%(rank)s
<nav class="secmenu">
 <a href="#whales">큰손 랭킹</a>
 <a href="#switch">갈아타기 흐름</a>
 <a href="#digest">주요 종목 해설</a>
 <a href="stocks.html">전체 종목 순위 →</a>
</nav>
<p class="lead">%(lead)s</p>
%(whales)s
%(digest)s
%(foot)s
<script>
document.querySelectorAll('[data-rot]').forEach(b=>{
 const ps=[...b.querySelectorAll('.rp')];
 if(ps.length<2) return; let i=0;
 setInterval(()=>{ps[i].classList.remove('on'); i=(i+1)%%ps.length; ps[i].classList.add('on');},5000);
});
</script>
</div></body></html>""" % {
        "desc": desc, "css": base_css, "nav": _nav("home"),
        "ndocs": format(ndocs, ","), "npeople": format(len(person_port), ","),
        "a_all": fmt_krw(asset_totals.get("all")), "a_sec": fmt_krw(asset_totals.get("sec")),
        "a_est": fmt_krw(asset_totals.get("estate")), "a_coin": fmt_krw(asset_totals.get("coin")),
        "rank": rank_html, "lead": lead, "nsearch": format(len(ppx), ","), "whales": whales_html, "digest": digest_html, "foot": foot_html,
    }

    # ── ③ 공직자 검색 페이지 (people.html) ──
    people_css = base_css + """
 input{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:8px;font-size:14px;margin-bottom:12px;background:#fff;color:var(--ink)}
 input:focus{outline:2px solid var(--accent-2);outline-offset:-1px}
 .tblwrap{background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden;overflow-x:auto}
 table{width:100%;border-collapse:collapse}
 th,td{text-align:left;padding:9px 12px;font-size:13px;border-top:1px solid var(--line);white-space:nowrap}
 th{background:var(--tint);color:var(--muted);font-weight:600;border-top:none}
 td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
 tr.prow{cursor:pointer} tr.prow:hover{background:#f0f3f8}
 .up{color:var(--up)} .down{color:var(--down)}
 .pcard{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:16px}
 .pcard h2{margin:0 0 2px;font-size:20px;color:var(--accent)}
 .pcard .msub{color:var(--muted);font-size:13px;margin-bottom:14px}
 .pcard h3{font-size:13px;color:var(--accent-2);margin:16px 0 8px}
 .orgbar{display:flex;flex-direction:column;gap:6px}
 .orgrow{display:grid;grid-template-columns:110px 1fr 90px;align-items:center;gap:8px;font-size:12px}
 .bar{height:14px;background:linear-gradient(90deg,var(--accent),var(--accent-2));border-radius:4px;min-width:2px}
 .bv{text-align:right;font-variant-numeric:tabular-nums;color:var(--accent);font-weight:600}
 .pcols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
 .plist{margin:0;padding-left:18px} .plist li{font-size:13px;line-height:1.9}
 .plist a{color:var(--accent-2);text-decoration:none} .plist a:hover{text-decoration:underline}
 .muted{color:var(--muted)}
 .newsbox{border-radius:10px;padding:12px 15px;margin-top:16px}
 .nbx-news{background:#eef6fc;border:1px solid #cfe3f4}
 .nbx-news h3{color:#0d5c8c!important} .nbx-news .nd{color:#7d9ab0}
 .newsbox h3{margin-top:0!important} .newsbox ul{margin:0;padding-left:18px} .newsbox li{font-size:13px;line-height:1.8}
 .newsbox a{color:var(--ink);text-decoration:none} .newsbox a:hover{color:var(--accent-2);text-decoration:underline}
 .newsbox .nd{color:var(--muted);font-size:11px;margin-left:6px} .newsbox .nsrc{color:var(--muted);font-size:11px;margin:8px 0 0}
 .note{color:var(--muted);font-size:12px;margin-top:10px;line-height:1.6}
 @media(max-width:700px){.pcols{grid-template-columns:1fr} .orgrow{grid-template-columns:90px 1fr 80px}}
"""
    people_html = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>공직자 검색 · 같이투자</title>
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9186959187584058" crossorigin="anonymous"></script>
<meta name="description" content="고위공직자 이름·소속으로 검색해 신고 재산 총액, 전년 대비 증감, 자산 구성, 주식 포트폴리오를 확인합니다.">
<style>%(css)s</style></head><body><div class="wrap">
%(nav)s
<h1>공직자 검색</h1>
<p class="tagsub">이름 또는 소속으로 찾아보세요</p>
<p class="sub">2026년 정기 재산공개(2025년말 기준) · 공직자 %(np)s명 · 기본 정렬은 총 신고재산 순(상위 50명 표시)</p>
<input id="q" placeholder="이름 또는 소속 검색 (예: 이세웅, 기획재정부)">
<div id="panel"></div>
<div class="tblwrap"><table><thead><tr><th>성명</th><th>소속</th><th>직위</th><th class="num">총 신고재산</th><th class="num">전년 대비</th></tr></thead><tbody id="list"></tbody></table></div>
<p class="note">금액은 공직자윤리법상 신고가액 · 행을 누르면 상세(자산 구성·주식 포트폴리오)가 열립니다 · 동명이인은 구분되지 않을 수 있습니다.</p>
%(foot)s
</div>
<script>
const P=__PPX__;
const PNEWS=__PNEWS__; // {people:{성명:[{t,l,d}]}, updated}
const CATLABEL={"부동산에관한규정이준용되는권리와자동차·건설기계·선박및항공기":"차량·권리","정치자금법에따른정치자금의수입및지출을위한예금계좌의예금":"정치자금 예금","합명·합자·유한회사출자지분":"출자지분","금및백금":"금·백금","골동품및예술품":"골동품·예술품","비영리법인에출연한재산":"출연재산"};
const rows=Object.entries(P).map(([n,d])=>({n,o:d.o||'',j:d.j||'',nw:d.nw||0,pv:d.pv}));
rows.sort((a,b)=>b.nw-a.nw);
function fmtKrw(chun){
 if(chun==null||!chun) return '-';
 const man=chun/10;
 if(man>=1e8){const v=man/1e8;return (v>=100?Math.round(v).toLocaleString():+v.toFixed(1))+'조원';}
 if(man>=1e4){const v=man/1e4;return (v>=100?Math.round(v).toLocaleString():+v.toFixed(1))+'억원';}
 return Math.round(man).toLocaleString()+'만원';
}
const q=document.getElementById('q'), list=document.getElementById('list'), panel=document.getElementById('panel');
function renderList(){
 const kw=q.value.trim();
 const f=kw?rows.filter(r=>r.n.includes(kw)||r.o.includes(kw)):rows;
 list.innerHTML=f.slice(0,50).map(r=>{
  const d=(r.pv===undefined||!r.nw)?null:(r.nw-r.pv);
  const dtxt=d===null?'-':((d>=0?'+':'-')+fmtKrw(Math.abs(d)));
  const cls=d===null?'':(d>0?'up':(d<0?'down':''));
  return `<tr class="prow" data-p="${r.n}"><td>${r.n}</td><td>${r.o}</td><td>${r.j}</td><td class="num">${fmtKrw(r.nw)}</td><td class="num ${cls}">${dtxt}</td></tr>`;
 }).join('')||'<tr><td colspan="5" class="muted">검색 결과가 없습니다</td></tr>';
}
function show(p){
 const d=P[p]; if(!d) return;
 const cats=Object.entries(d.a||{}).filter(([c])=>c!=='채무').sort((x,y)=>y[1]-x[1]);
 const mx=cats.length?cats[0][1]:1;
 const bars=cats.map(([c,v])=>`<div class="orgrow"><span>${CATLABEL[c]||c}</span><span class="bar" style="width:${Math.max(2,Math.round(v/mx*100))}%%"></span><span class="bv">${fmtKrw(v)}</span></div>`).join('');
 const debt=(d.a||{})['채무'];
 const li=(arr)=>arr.length?arr.map(x=>`<li><a href="stocks.html#s=${encodeURIComponent(x[0])}">${x[0]}</a><span class="wv">${x[1].toLocaleString()}주</span></li>`).join(''):'<li class="muted">없음</li>';
 const dt=(d.nw&&d.pv!==undefined)?(d.nw-d.pv):null;
 panel.innerHTML=`<div class="pcard">
  <h2>${p}</h2><div class="msub">${[d.o,d.j].filter(Boolean).join(' · ')||'-'}</div>
  <div class="stat">
   <div class="card"><div class="lab">총 신고재산</div><div class="val">${fmtKrw(d.nw)}</div></div>
   <div class="card"><div class="lab">전년 대비</div><div class="val ${dt>0?'up':(dt<0?'down':'')}">${dt===null?'-':(dt>=0?'+':'-')+fmtKrw(Math.abs(dt))}</div></div>
   <div class="card"><div class="lab">증권 신고액</div><div class="val">${fmtKrw(d.sec)}</div></div>
   ${debt?`<div class="card"><div class="lab">채무</div><div class="val down">${fmtKrw(debt)}</div></div>`:''}
  </div>
  <h3>자산 구성 (신고가액)</h3>
  <div class="orgbar">${bars||'<span class="muted">내역 없음</span>'}</div>
  <div class="pcols">
   <div><h3>국내주식 ${d.d.length}종목</h3><ul class="plist">${li(d.d)}</ul></div>
   <div><h3>해외·기타 ${d.v.length}종목</h3><ul class="plist">${li(d.v)}</ul></div>
  </div>
  ${(PNEWS.people&&PNEWS.people[p]&&PNEWS.people[p].length)?`<div class="newsbox nbx-news"><h3>관련 뉴스</h3><ul>${PNEWS.people[p].map(n=>`<li><a href="${n.l}" target="_blank" rel="noopener">${n.t}</a><span class="nd">${n.d}</span></li>`).join('')}</ul><p class="nsrc">네이버 뉴스 검색(제목·링크만 표시) · 동명이인 뉴스가 섞일 수 있습니다 · ${PNEWS.updated||''} 수집</p></div>`:''}
  <p class="note">가족 명의 신고분 포함 · 종목을 누르면 종목 상세로 이동합니다.</p>
 </div>`;
 panel.scrollIntoView({behavior:'smooth',block:'start'});
}
list.addEventListener('click',e=>{const tr=e.target.closest('tr.prow'); if(tr) show(tr.dataset.p);});
q.oninput=renderList;
// 엔터 → 재검색: 목록 갱신 + 첫 번째 결과의 상세를 바로 연다 (다른 사람 검색 후 즉시 전환)
q.addEventListener('keydown',e=>{
 if(e.key!=='Enter') return;
 e.preventDefault(); renderList();
 const first=list.querySelector('tr.prow');
 if(first) show(first.dataset.p); else panel.innerHTML='';
});
const _sp=new URLSearchParams(location.search);
const _kw=_sp.get('q'); if(_kw) q.value=_kw;
renderList();
const _pn=_sp.get('p'); if(_pn&&P[_pn]) show(_pn);
</script></body></html>""" % {"css": people_css, "nav": _nav("people"),
                              "np": format(len(ppx), ","), "foot": foot_html}
    people_html = people_html.replace("__PPX__", json.dumps(ppx, ensure_ascii=False))
    people_html = people_html.replace("__PNEWS__", json.dumps(
        {"people": newsdata.get("people", {}), "updated": newsdata.get("updated")}, ensure_ascii=False))

    # ── ④ 칼럼 페이지 (column.html) — AI 리서처 '리나'의 주간 데이터 해설 ──
    columns = (json.load(open("columns.json", encoding="utf-8"))
               if os.path.exists("columns.json") else [])
    col_items = []
    for i, c in enumerate(columns):
        body = '<div class="colhead"><span class="coldate">%s</span></div><h2 class="coltitle">%s</h2>%s' % (
            c.get("d", ""), c.get("t", ""), c.get("b", ""))
        if i == 0:
            col_items.append('<article class="colcard">%s</article>' % body)
        else:
            col_items.append('<details class="colcard colprev"><summary>%s <span class="coldate">%s</span></summary>%s</details>'
                             % (c.get("t", ""), c.get("d", ""), c.get("b", "")))
    col_list = "".join(col_items) if col_items else '<p class="muted">첫 칼럼을 준비 중입니다.</p>'
    column_css = base_css + """
 .rina{background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:0 0 20px}
 .rina .rphoto{width:100%;max-height:360px;object-fit:cover;object-position:center 18%;display:block}
 .rina .rbody{padding:16px 20px 18px}
 .rina h2{margin:0 0 4px;font-size:17px;color:var(--accent);padding:0} .rina h2::before{display:none}
 .rina p{margin:0;font-size:13.5px;line-height:1.75;color:var(--muted)}
 .rina .style{color:var(--accent-2);font-weight:600}
 .colcard{background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-bottom:14px;font-size:14.5px;line-height:1.9}
 .colcard .coldate{color:var(--muted);font-size:12.5px}
 .coltitle{font-size:19px;color:var(--accent);margin:4px 0 14px;padding:0} .coltitle::before{display:none}
 .colcard p{margin:0 0 12px} .colcard b{color:var(--accent)}
 .colcard ul{margin:0 0 12px;padding-left:20px} .colcard li{margin-bottom:5px}
 details.colprev summary{cursor:pointer;font-weight:700;color:var(--accent);font-size:15.5px}
 details.colprev[open] summary{margin-bottom:12px}
 .disclaim{background:var(--tint);border:1px solid #d6e0ee;border-radius:10px;padding:12px 15px;font-size:12.5px;color:var(--muted);line-height:1.8;margin-top:18px}
"""
    column_html = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>칼럼 · 같이투자</title>
<meta name="description" content="AI 리서처 리나가 매주 공직자 재산공개 데이터에서 읽어낸 흐름을 해설합니다. 투자 권유가 아닌 데이터 해설 칼럼입니다.">
%(ads)s
<style>%(css)s</style></head><body><div class="wrap">
%(nav)s
<h1>리나의 주간 리서치</h1>
<p class="tagsub">공직자 포트폴리오 데이터에서 읽어낸 이번 주의 흐름</p>
<div class="rina">
 <img class="rphoto" src="rina.jpg" alt="AI 리서처 리나">
 <div class="rbody">
  <h2>리나 (RINA) · AI 리서처</h2>
  <p>같이투자의 데이터를 매주 읽고 해설하는 AI입니다. 성향은 <span class="style">원금 보전을 중시하는 보수형이지만,
  데이터가 뒷받침되면 중위험까지 들여다보는 편</span>입니다. 종목을 추천하지 않으며, 공개 데이터가 말해주는
  것과 말해주지 않는 것을 구분해 전하는 것이 제 일입니다.</p>
 </div>
</div>
%(cols)s
<div class="disclaim">본 칼럼은 공직자윤리법에 따라 공개된 재산 데이터에 대한 <b>해설·논평</b>이며,
특정 종목의 매수·매도를 권유하지 않습니다. 리나(RINA)는 AI이며, 글은 발행 시점의 공개 데이터를 기반으로
자동 작성 후 게시됩니다. 투자 판단과 책임은 이용자 본인에게 있습니다.</div>
%(foot)s
</div></body></html>""" % {"css": column_css, "nav": _nav("column"), "cols": col_list, "foot": foot_html,
                           "ads": '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9186959187584058" crossorigin="anonymous"></script>'}

    return {"index": index_html, "stocks": stocks_html, "people": people_html, "column": column_html}


# ── 상세 파싱 공용 정규식/헬퍼 (cmd_parse 및 검증 스크립트가 공유) ──
MONEY4 = re.compile(r"\s+(?:-|[\d,]+)\s+(?:-|[\d,]+)\s+(?:-|[\d,]+)\s+(?:-|[\d,]+)")
# 소속이 빈 경우(대통령), 대법원 관보의 띄어쓰기('소 속 … 성 명 조 희 대'),
# 국회공보의 무공백('소속국회 직위국회의원성명안철수')을 모두 매칭.
# 이름은 항상 뒤따르는 '(단위'를 앵커로 캡처(공백 포함 가능 → person_name()으로 정리).
HEADER = re.compile(r"소\s?속\s*(.{0,60}?)\s*직\s?위\s*(.{1,40}?)\s*성\s?명\s*([^(]{1,20}?)\s*\(단위")
NAMEONLY = re.compile(r"성\s?명\s*([^(]{1,20}?)\s*\(단위")

# 국회공보는 표 셀 경계 공백이 소실되어 금액 4컬럼이 붙어 나옴("123,132,000011,346,000…").
# 천단위 콤마 형식을 이용해 붙은 숫자 덩어리를 개별 금액으로 복원한다.
_NUMTOK = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")


def split_glued_nums(s):
    """'129,604,7702,544,93317,818,770114,330,933' → ['129,604,770','2,544,933','17,818,770','114,330,933']"""
    return _NUMTOK.findall(s or "")


def split4_by_identity(s):
    """붙은 4컬럼 금액(종전·증가·감소·현재)을 항등식 a+b-c=d 로 복원.
    콤마 복원만으로는 '0' 컬럼이 경계를 파괴하거나(894,209|6,627|0|900,836)
    콤마 자체가 소실된 장숫자를 못 다루므로, 전수 4분할 중 항등식을 만족하는
    분할을 찾는다. 콤마 패턴과 정확히 일치하는 해가 있으면 우선. 실패 시 None."""
    digits = re.sub(r"[^\d]", "", s or "")
    if not digits or len(digits) < 4 or len(digits) > 34:
        return None
    # 콤마가 전혀 없는 장숫자는 분할 근거가 없어 임의 해 위험 → 스킵(전부 0이면 예외)
    if "," not in (s or "") and set(digits) != {"0"}:
        return None
    n = len(digits)
    cands = []
    for i in range(1, n - 2):
        for j in range(i + 1, n - 1):
            for k in range(j + 1, n):
                p = (digits[:i], digits[i:j], digits[j:k], digits[k:])
                if any(len(x) > 1 and x[0] == "0" for x in p):
                    continue
                a, b, c, d = (int(x) for x in p)
                if a + b - c == d:
                    cands.append((a, b, c, d))
    if not cands:
        return None
    orig = re.sub(r"\s", "", s or "")
    for t in cands:
        if "".join(format(x, ",") for x in t) == orig:
            return t
    return cands[0]


# 종목 나열 뒤에 공백 후 10자리 이상 콤마숫자 덩어리(붙은 금액 컬럼)가 오면 거기서 컷 (국회공보용)
MONEY_GLUED = re.compile(r"\s[\d,]{10,}")
# 금액이 1컬럼뿐인 공보(국회 신규·퇴직 호 등): '…N주 11,604 …'처럼 '주'/')' 뒤 단독 금액에서 컷.
# 숫자 뒤가 '주'( 공백 포함: '1,358 주')면 수량이므로 제외.
MONEY_TAIL = re.compile(r"(?<=[주)])\s+[\d,]{3,}(?=\s|$)(?!\s?주)")
# 닫는 괄호 바로 뒤 공백 없이 금액이 붙는 경우('(48주감소)19,059…') — 국회공보 셀 공백 소실 변형
MONEY_STUCK = re.compile(r"(?<=\))[\d,]{4,}")


def person_name(raw):
    """헤더에서 캡처한 성명 정리: '조 희 대'→'조희대', '존 리'→'존리'."""
    return re.sub(r"\s+", "", raw or "")
SEC = re.compile(r"증권\(소계\)\s+(?:-|[\d,]+)\s+(?:-|[\d,]+)\s+(?:-|[\d,]+)\s+([\d,]+)")
# 자산 대분류 소계 행: '▶ 토지(소계) 종전 증가 감소 현재' → (분류명, 현재가액). 채무 포함 전 분류 공통.
SUBTOT = re.compile(r"▶\s*([가-힣A-Za-z·\s]+?)\s*\(\s*소\s*계\s*\)\s+(?:-|[\d,]+)\s+(?:-|[\d,]+)\s+(?:-|[\d,]+)\s+(-|[\d,]+)")
# 국회공보 폴백: 금액 4컬럼이 붙어 나오는 경우 — 숫자 구간을 통째 캡처해 split_glued_nums로 복원
SUBTOT2 = re.compile(r"▶\s*([가-힣A-Za-z·\s]+?)\s*\(\s*소\s*계\s*\)\s*([\d,\s]{7,}?)(?=[가-힣▶]|$)")
# 재산 총계 행: '총 계 종전 증가 감소 현재' → (종전, 현재). '올해 불어난 재산' 랭킹용.
TOTALROW = re.compile(r"총\s*계\s+(-|[\d,]+)\s+(?:-|[\d,]+)\s+(?:-|[\d,]+)\s+([\d,]+)")
TOTALROW2 = re.compile(r"총\s*계\s*([\d,\s]{7,}?)(?=[가-힣▶(]|$)")


LISTED_KW = re.compile(r"(?<!비)상장주식")
BLOCK_END = re.compile(r"비상장주식|▶|채권\(소계\)|총계")


def details_of(text):
    """페이지 텍스트에서 '상장주식' 블록만 뽑아 종목 나열 문자열 리스트로 반환.
    금액 4개 컬럼(MONEY4)을 잘라내고, 개행→공백·증감 표기 정리·관계어 접두 제거.

    ※ 한 사람의 재산표에는 본인·배우자·자녀별로 '상장주식' 행이 여러 개 나올 수 있고
      각 행 뒤에 자기 금액 컬럼이 붙는다. 예전엔 첫 '상장주식'부터 다음 ▶/총계까지를
      통째로 잡고 MONEY4로 '첫' 금액에서 잘라, 본인분만 남고 배우자·자녀분이 통째로
      버려졌다(집계 대량 누락). 이제 '상장주식' 출현마다 개별 블록으로 끊어 각 블록을
      자기 금액 컬럼에서 잘라 관계별 목록을 모두 살린다."""
    out = []; blob = text.replace("\n", " ")
    starts = [m.end() for m in LISTED_KW.finditer(blob)]
    for idx, si in enumerate(starts):
        end = len(blob)
        bm = BLOCK_END.search(blob, si)          # 다음 소계/구분 경계
        if bm:
            end = min(end, bm.start())
        if idx + 1 < len(starts):                # 다음 '상장주식' 블록 시작 전까지
            end = min(end, starts[idx + 1] - len("상장주식"))
        seg = blob[si:end]
        # 이 블록 자신의 금액 컬럼에서 컷: 공백 4컬럼(관보) / 붙은 금액 덩어리 / 단독 1컬럼 금액(국회)
        cuts = [m.start() for m in (MONEY4.search(seg), MONEY_GLUED.search(seg),
                                    MONEY_TAIL.search(seg), MONEY_STUCK.search(seg)) if m]
        d = (seg[:min(cuts)] if cuts else seg)
        d = re.sub(r"증\s+가", "증가", d); d = re.sub(r"감\s+소", "감소", d)
        d = re.sub(r"^\s*(본인|배우자|장남|차남|장녀|차녀|모|부|자녀)\s+", "", d.strip())
        d = re.sub(r"\s{2,}", " ", d)
        if d:
            out.append(d)
    return out


def file_year(fn):
    """파일명 ..._YYYYMMDD_... 의 발행연도 → 기준연도(전년말)."""
    m = re.search(r"_(\d{8})_", fn)
    return int(m.group(1)[:4]) - 1 if m else None


def cmd_parse():
    try:
        from pypdf import PdfReader
        from holdings_parser import parse_securities, normalize
    except Exception as e:
        print("의존성 필요: pip3 install pypdf, 그리고 holdings_parser.py 를 같은 폴더에.", e); return
    from collections import defaultdict

    if not os.path.isdir(PDF_DIR):
        print("%s/ 없음. 먼저 download 하세요." % PDF_DIR); return
    load_krx()
    prices = json.load(open(PRICE_FILE, encoding="utf-8")) if os.path.exists(PRICE_FILE) else {}

    files = [f for f in os.listdir(PDF_DIR) if f.endswith(".pdf")]
    yrs = [file_year(f) for f in files if file_year(f)]
    latest = max(yrs) if yrs else None
    print("파싱 대상 PDF %d개 (연도 %s~%s, 최신 기준연도=%s)"
          % (len(files), min(yrs) if yrs else "?", max(yrs) if yrs else "?", latest))

    # 최신 연도 상세(메인 순위/모달용)
    dom_detail = defaultdict(dict)
    ovs_detail = defaultdict(dict)
    person_sec = {}   # 성명 -> 증권(소계) 현재가액(천원)  ※최신연도
    # 시계열: key -> {기준연도: set(성명)}
    ts = defaultdict(lambda: defaultdict(set))
    newbuy = defaultdict(set)   # 최신연도 신규 매수(증가분==보유량, 즉 0주→매수) 성명 집합
    soldout = defaultdict(set)  # 최신연도 전량 매도(0주 감소) 성명 집합
    def _nofile(s):
        """헤더 미매칭 시 남는 파일명이 소속/직위로 새는 것 차단."""
        return "" if (not s or s.endswith(".pdf") or re.search(r"_\d{8}_", s)) else s

    person_meta = {}            # 성명 -> (소속, 직위)  ※최신연도, 풀헤더 기준(정확)
    person_loose = {}           # 성명 -> (소속, 직위)  ※느슨한 추적 폴백(종목 상세와 동일 신뢰수준)
    person_assets = defaultdict(dict)  # 성명 -> {자산분류: 현재가액(천원), _prev/_now: 총계}
    year_docs = defaultdict(int)

    for fn in files:
        yr = file_year(fn) or latest
        year_docs[yr] += 1
        try:
            r = PdfReader(os.path.join(PDF_DIR, fn))
        except Exception as e:
            print("  PDF 열기 실패:", fn, e); continue
        org = pos = person = fn
        four_col = True
        for p in r.pages:
            t = p.extract_text() or ""
            blob = t.replace("\n", " ")
            # 성명은 느슨하게 우선 갱신(인식률↑), 소속·직위는 풀헤더 있을 때만 갱신
            nm = NAMEONLY.search(blob)
            if nm:
                person = person_name(nm.group(1))
            hd = HEADER.search(blob)
            if hd:
                org, pos, person = hd.group(1).strip(), hd.group(2).strip(), person_name(hd.group(3))
            if yr == latest:
                if hd:
                    person_meta[person] = (org, pos)
                sm = SEC.search(blob)
                if sm:
                    try:
                        person_sec[person] = int(sm.group(1).replace(",", ""))
                    except ValueError:
                        pass
                # 자산 대분류 소계(토지/건물/예금/증권/가상자산/채무 등) + 재산 총계
                def _n(s):
                    return 0 if s == "-" else int(s.replace(",", ""))
                matched = set()
                # 정기형(4컬럼)/최초등록형(1컬럼) 판별 — 표가 여러 페이지에 걸치므로 페이지가 아닌
                # '사람' 단위 상태로 유지: 헤더('종전가액')가 보이면 4컬럼, 새 사람 시작인데 없으면 1컬럼,
                # 연속 페이지(헤더 없음)는 직전 상태 유지.
                if "종전가액" in blob:
                    four_col = True
                elif hd:
                    four_col = False
                for m in SUBTOT.finditer(blob):
                    cat = m.group(1).replace(" ", "")
                    person_assets[person][cat] = _n(m.group(2))
                    matched.add(cat)
                for m in SUBTOT2.finditer(blob):     # 붙은 금액(국회공보)·1컬럼(최초등록) 폴백
                    cat = m.group(1).replace(" ", "")
                    if cat in matched:
                        continue
                    raw = m.group(2)
                    toks = split_glued_nums(raw)
                    val = None
                    if four_col:
                        # 항등식(종전+증가-감소=현재)으로 분할 검증·복원
                        q = split4_by_identity(raw)
                        if q:
                            val = q[3]
                        elif len(toks) >= 4:
                            val = _n(toks[3])
                    else:
                        if len(toks) == 1:
                            val = _n(toks[0])
                    if val is not None:
                        person_assets[person][cat] = val
                tm = TOTALROW.search(blob)
                if tm:
                    person_assets[person]["_prev"] = _n(tm.group(1))
                    person_assets[person]["_now"] = _n(tm.group(2))
                else:
                    tm2 = TOTALROW2.search(blob)
                    if tm2:
                        raw = tm2.group(1)
                        toks = split_glued_nums(raw)
                        if four_col:
                            q = split4_by_identity(raw)
                            if q:
                                person_assets[person]["_prev"] = q[0]
                                person_assets[person]["_now"] = q[3]
                            elif len(toks) >= 4:
                                person_assets[person]["_prev"] = _n(toks[0])
                                person_assets[person]["_now"] = _n(toks[3])
                        elif len(toks) == 1:          # 최초등록형: 총계 1개(전년 없음)
                            person_assets[person]["_now"] = _n(toks[0])
                person_loose[person] = (_nofile(org), _nofile(pos))
            for d in details_of(t):
                for h in parse_securities(d):
                    key = normalize(h.name)
                    if h.qty in (None, 0):
                        # 0주(N주 감소) = 이번 회차에 전량 매도 → '던진 공직자'로 집계(최신연도)
                        if yr == latest and h.sold_out and h.direction == "감소":
                            soldout[key].add(person)
                        continue
                    ts[key][yr].add(person)
                    if yr == latest:
                        # 증가분이 현재 보유량과 같으면 = 0주에서 새로 담음(신규 매수)
                        if h.direction == "증가" and h.change is not None and h.change == h.qty:
                            newbuy[key].add(person)
                        detail = dom_detail if h.category == "국내상장" else ovs_detail
                        rec = detail[key].setdefault(person, {"o": _nofile(org), "j": _nofile(pos), "q": 0})
                        rec["q"] += h.qty

    # SEC 정규식이 못 잡은 인물(국회공보 등)은 자산 소계의 '증권' 값으로 폴백
    for p, a in person_assets.items():
        if "증권" in a and p not in person_sec:
            person_sec[p] = a["증권"]

    # 수시 최초등록형(전년 총계 없음)은 관보 총계 숫자가 추출 중 일부 소실되는 사례가 있어
    # 자산 소계 합(-채무)과 30% 이상 어긋나면 소계 합으로 보정
    for p, a in person_assets.items():
        if "_prev" not in a:
            cats_sum = sum(v for c, v in a.items() if not c.startswith("_") and c != "채무")
            calc = cats_sum - a.get("채무", 0)
            now = a.get("_now")
            if calc > 0 and (now is None or now <= 0 or abs(now - calc) / calc > 0.3):
                a["_now"] = calc

    def rank(detail):
        rows = []
        for k, people in detail.items():
            cnt = len(people)
            tot = sum(v["q"] for v in people.values())
            rows.append((k, people, tot, cnt))
        rows.sort(key=lambda x: (-x[3], -x[2]))
        return rows

    dom_r = rank(dom_detail)
    ovs_r = rank(ovs_detail)
    # (종목, 보유자집합, 총주수) 형태로 변환 (기존 build_html/save_csv 호환)
    dom = [(k, set(people.keys()), tot) for k, people, tot, cnt in dom_r]
    ovs = [(k, set(people.keys()), tot) for k, people, tot, cnt in ovs_r]

    # 인물 포트폴리오: 성명 → {소속, 직위, 증권소계, 국내(d)/해외(v) [종목, 주수] 목록}
    #   (전 종목 대상. 종목 상세 모달에서 이름 클릭 → 개인 포트폴리오 화면으로 사용)
    person_port = {}
    for detail, slot in ((dom_detail, "d"), (ovs_detail, "v")):
        for k, people in detail.items():
            for nm, v in people.items():
                p = person_port.setdefault(nm, {"o": v["o"], "j": v["j"], "d": [], "v": []})
                q = v["q"]
                # 소수점 주식(0.5주 등)은 절사하면 0주로 보이므로 소수 2자리 유지
                p[slot].append([k, round(q, 2) if q < 1 else int(round(q))])
    for nm, p in person_port.items():
        p["d"].sort(key=lambda x: -x[1])
        p["v"].sort(key=lambda x: -x[1])
        sec = person_sec.get(nm)
        if sec:
            p["sec"] = sec

    # ── 큰손 랭킹(자산 소계 기반) + 전체 합계 + 갈아타기 ──
    def _who_of(p):
        if p in person_meta:
            return person_meta[p]
        if p in person_loose:
            return person_loose[p]
        pp = person_port.get(p)
        return (pp["o"], pp["j"]) if pp else ("", "")

    def _org_of(p):
        return _who_of(p)[0]

    def top_by(fn, n=10):
        rows = []
        for p, a in person_assets.items():
            v = fn(a)
            if v and v > 0:
                rows.append((v, p))
        rows.sort(reverse=True)
        out = []
        for v, p in rows[:n]:
            out.append({"n": p, "o": _org_of(p), "v": int(v), "pp": 1 if p in person_port else 0})
        return out

    whales = {
        "stock":  ("주식 큰손",  "증권 신고액", top_by(lambda a: a.get("증권", 0))),
        "estate": ("부동산 큰손", "토지+건물 신고액", top_by(lambda a: a.get("토지", 0) + a.get("건물", 0))),
        "coin":   ("코인 큰손",  "가상자산 신고액", top_by(lambda a: a.get("가상자산", 0))),
        "cash":   ("현금 큰손",  "예금+현금 신고액", top_by(lambda a: a.get("예금", 0) + a.get("현금", 0))),
        "gain":   ("올해 가장 불어난 재산", "총재산 증가액(전년 대비)",
                   top_by(lambda a: a.get("_now", 0) - a.get("_prev", 0) if a.get("_now") else 0)),
    }
    asset_totals = {
        "people": len(person_assets),
        "all":    sum(a.get("_now", 0) for a in person_assets.values()),
        "sec":    sum(a.get("증권", 0) for a in person_assets.values()),
        "estate": sum(a.get("토지", 0) + a.get("건물", 0) for a in person_assets.values()),
        "coin":   sum(a.get("가상자산", 0) for a in person_assets.values()),
    }
    # 갈아타기: 많이 던진 종목 × 많이 새로 담은 종목의 동일 인물 교집합
    tsold = sorted(soldout.items(), key=lambda kv: -len(kv[1]))[:20]
    tnewb = sorted(newbuy.items(), key=lambda kv: -len(kv[1]))[:20]
    switches = []
    for sk, sv in tsold:
        for bk, bv in tnewb:
            if sk == bk:
                continue
            c = len(sv & bv)
            if c >= 5:
                switches.append((c, sk, bk))
    switches.sort(reverse=True)
    switches = switches[:3]

    # 최다 종목 보유: 국내+해외 상장주식 보유 종목 수 상위
    # (※ 기존 '집중투자'(한 종목 비중)는 분자=시가·분모=신고가의 기준 불일치로 분산투자자도
    #    100%가 되는 구조적 왜곡이 있어 폐기 — 2026-07 검증)
    focus = sorted(((len(pp["d"]) + len(pp["v"]), nm) for nm, pp in person_port.items()),
                   reverse=True)
    focus = [{"n": nm, "o": _org_of(nm), "w": cnt, "pp": 1} for cnt, nm in focus[:10]]

    # 인물 검색 페이지용 확장 데이터: 주식 포트폴리오 + 자산 구성 + 총계 증감
    ppx = {}
    for p in (set(person_assets) | set(person_port)):
        a = person_assets.get(p, {})
        port = person_port.get(p, {"d": [], "v": []})
        o, j = _who_of(p)
        e = {"o": o, "j": j, "d": port.get("d", []), "v": port.get("v", []),
             "a": {c: v for c, v in a.items() if not c.startswith("_") and v}}
        if a.get("_now") is not None:
            e["nw"] = a["_now"]
        if a.get("_prev") is not None:
            e["pv"] = a["_prev"]
        sec = person_sec.get(p)
        if sec:
            e["sec"] = sec
        ppx[p] = e

    def save_csv(fname, rows):
        with open(fname, "w", encoding="utf-8-sig") as f:
            f.write("순위,종목,보유인원,총주수\n")
            for i, (k, who, sh) in enumerate(rows, 1):
                f.write("%d,%s,%d,%d\n" % (i, k, len(who), int(sh)))

    save_csv("holdings_domestic.csv", dom)
    save_csv("holdings_overseas.csv", ovs)

    # 상세 데이터: 상위 200종목만 임베드(파일 크기 관리).
    def detail_payload(ranked, topn=200):
        out = {}
        for k, people, tot, cnt in ranked[:topn]:
            hs = sorted(people.items(), key=lambda kv: -kv[1]["q"])
            orgs = defaultdict(int)
            for _, v in people.items():
                orgs[v["o"]] += 1
            entry = {
                "orgs": sorted(orgs.items(), key=lambda x: -x[1]),
                "holders": [{"o": v["o"], "j": v["j"], "n": nm, "q": int(v["q"])} for nm, v in hs],
            }
            # 이번 회차 신규 매수/전량 매도 (자체 증감 표기 기반)
            nb = newbuy.get(k); so = soldout.get(k)
            if nb:
                entry["nnew"] = len(nb)
                for hd in entry["holders"]:      # 현재 보유자 중 신규 매수자 표시
                    if hd["n"] in nb:
                        hd["new"] = 1
            if so:
                entry["nout"] = len(so)
            # 포트폴리오 비중: (보유주수 × 기준일 종가) / 본인 증권소계
            price = (prices.get(k) or {}).get("p2025")
            if price:
                ws = []
                for nm, v in people.items():
                    sec = person_sec.get(nm)  # 천원 단위
                    if sec and sec > 0:
                        w = (v["q"] * price) / (sec * 1000.0)
                        ws.append(min(w, 1.0))
                if ws:
                    entry["wt"] = round(sum(ws) / len(ws) * 100, 1)      # 평균 비중(%)
                    entry["wtn"] = len(ws)                                # 계산 가능 인원
            out[k] = entry
        return out

    # 시계열: {종목: [[연도, 보유자수], ...]} — 2년 이상 데이터가 있는 종목만
    ts_payload = {}
    embed_keys = set(list(detail_payload(dom_r).keys()) + list(detail_payload(ovs_r).keys()))
    for k in embed_keys:
        if k in ts:
            series = sorted((yr, len(people)) for yr, people in ts[k].items())
            if len(series) >= 2:
                ts_payload[k] = series

    pages = build_site(dom, ovs, prices,
                       detail_payload(dom_r), detail_payload(ovs_r), ts_payload,
                       person_port=person_port, whales=whales,
                       asset_totals=asset_totals, switches=switches,
                       focus=focus, ppx=ppx, ndocs=len(files))
    for fname, content in (("index.html", pages["index"]),
                           ("stocks.html", pages["stocks"]),
                           ("people.html", pages["people"]),
                           ("column.html", pages["column"])):
        open(fname, "w", encoding="utf-8").write(content)

    # 뉴스 수집 대상(상위 30종목×2 + 큰손/집중투자 인물) → cmd_news 가 사용
    tg_people = {}
    for _t, _s, rows in whales.values():
        for r in rows:
            tg_people[r["n"]] = r.get("o", "")
    for r in focus:
        tg_people[r["n"]] = r.get("o", "")
    json.dump({"stocks": [k for k, _, _ in dom[:30]] + [k for k, _, _ in ovs[:30]],
               "people": [{"n": n, "o": o} for n, o in tg_people.items()]},
              open("news_targets.json", "w", encoding="utf-8"), ensure_ascii=False)

    yr_summary = ", ".join("%d년:%d건" % (y, year_docs[y]) for y in sorted(year_docs))
    print("\u2714 저장: holdings_domestic.csv(%d) / holdings_overseas.csv(%d) / index+stocks+people.html"
          % (len(dom), len(ovs)))
    print("  연도별 문서: %s | 시계열 종목: %d개" % (yr_summary, len(ts_payload)))
    print("\n[국내주식] 상위 15")
    for i, (k, who, sh) in enumerate(dom[:15], 1):
        print("  %2d. %-20s %4d명  %12s주" % (i, k[:18], len(who), format(int(sh), ",")))
    print("\n[해외·기타] 상위 15  (한글/영문 표기 통합 전)")
    for i, (k, who, sh) in enumerate(ovs[:15], 1):
        print("  %2d. %-20s %4d명  %12s주" % (i, k[:18], len(who), format(int(sh), ",")))
    print("\n→ index.html 을 더블클릭하면 브라우저에서 순위를 볼 수 있습니다.")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    rest = sys.argv[2:]
    if cmd == "index":
        cmd_index()
    elif cmd == "download":
        cmd_download(rest)
    elif cmd == "parse":
        cmd_parse()
    elif cmd == "assembly":
        cmd_assembly()
    elif cmd == "news":
        cmd_news()
    elif cmd == "krx":
        # 최신 KRX 전종목으로 사전 강제 재구축
        load_krx(force=True)
        print("→ 이제 python3 gwanbo_crawler.py parse 로 국내/해외 분류를 갱신하세요.")
    elif cmd == "price":
        cmd_price(rest)
    elif cmd == "all":
        cmd_index(); cmd_download(rest); cmd_parse()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
