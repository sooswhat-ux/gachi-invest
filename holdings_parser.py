# -*- coding: utf-8 -*-
"""
공직자 재산공개 '증권' 문자열 파서 (MVP 코어 엔진)

입력: 관보/공직윤리시스템 원본에 찍히는 증권 세부내역 문자열
출력: 종목명 / 수량 / 증감분 / 증감방향 / 전량매도여부 / KRX매칭 / 분류

이 파일 하나가 사이트의 심장입니다.
- 종목명에 붙은 괄호( 메타플랫폼스(페이스북), (주)KEB하나은행 )와
  증감 괄호( (15주 증가) )를 구분해서 잘라냅니다.
- 0주(전량 매도) 항목은 '보유 집계'에서 빼되, '던진 종목' 피처용으로 표시합니다.
- KRX 상장종목 사전에 매칭되면 국내주식으로 확정합니다.
  (이름이 한글이냐 영어냐로 추측하지 않습니다 — 사전 매칭이 먼저)
"""

from __future__ import annotations
import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# KRX 상장종목 사전 (데모용 스텁)
#   실제 운영에서는 KRX 전종목 목록(약 2,700개)을 CSV/pykrx로 로드해서 교체.
#   여기 코드값 중 유명 종목(안랩·삼성전자 등)만 확정값이고,
#   나머지는 '매칭 로직 증명용' 플레이스홀더입니다. 운영 시 공식 목록으로 검증.
# ---------------------------------------------------------------------------
KRX_LISTED = {
    "안랩": "053800",
    "삼성전자": "005930",
    "삼성전자우": "005935",          # 우선주는 보통주와 '다른 종목' — 절대 병합 금지
    "SK하이닉스": "000660",
    "현대차": "005380",
    "하이스틸": "071090",
    "뉴파워프라즈마": "073640",
    "제이아이테크": "090710",
    # 이름만 보면 외국 같지만 실제로는 KRX 상장 국내종목 — 사전 매칭으로만 잡힘
    "맥쿼리한국인프라투융자회사": "088980",
    "맥쿼리인프라": "088980",
}

# 증감 괄호:  (15주 증가) / (197,972 감소) / (1,340주 증가) / (0.0023주 증가)
CHANGE_RE = re.compile(r"\(\s*(\d[\d,]*(?:\.\d+)?)\s*주?\s*(증가|감소)\s*\)\s*$")
# 말미 수량:  1,860,000주 / 14주 / 186,934 / 0.0023주  (반드시 숫자로 시작)
QTY_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*주?\s*$")


def _num(s):
    """콤마 제거 후 정수면 int, 소수면 float. 숫자가 없으면 None."""
    s = (s or "").replace(",", "").strip()
    if not re.search(r"\d", s):
        return None
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return None


@dataclass
class Holding:
    raw: str            # 원본 조각
    name: str           # 종목명
    qty: int | None     # 현재 수량(주)
    change: int | None  # 증감분
    direction: str | None  # '증가' / '감소' / None
    sold_out: bool      # 0주 = 전량 매도
    krx_code: str | None
    category: str       # 국내상장 / 해외추정 / 미매칭


def split_items(s: str) -> list[str]:
    """
    아이템 구분자는 원칙적으로 '쉼표 + 공백'.
    천단위 쉼표(1,860,000)나 괄호 안 쉼표(197,972 감소)에서는 자르지 않는다.
    다만 원문에서 공백이 소실된 경우(예: '브로드컴5주,삼성생명18주(18주증가),샌디스크4주')와
    닫는 괄호가 누락된 경우('메타플랫폼스(페이스6주,미래에셋...')도 복구한다:
    직전 토큰이 'N주' 또는 ')'로 끝나고 쉼표 뒤가 종목명(한글/영문/'(')으로 시작하면 분리.

    ※ 관보 원문은 항목 사이에 공백이 있어('…6주, 미래에셋…') 쉼표 바로 뒤가 공백인데,
      앞 항목에 닫히지 않은 '('(예: '메타플랫폼스(페이스'는 '북)'가 소실됨)가 있으면
      depth가 0으로 돌아오지 못해 cond_space가 영영 막힌다. 그래서 복구 판정은
      쉼표 뒤 '공백을 건너뛴' 첫 의미있는 문자로 하고, depth와 무관하게 분리한다.
    """
    items, buf, depth, i = [], [], 0, 0
    while i < len(s):
        c = s[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth = max(0, depth - 1)
        if c == "," and i + 1 < len(s):
            cur = "".join(buf).rstrip()
            # 쉼표 뒤 선행 공백(스페이스/개행/탭 등)을 건너뛰어 다음 항목의 첫 문자를 본다.
            # 관보 PDF는 개행이 스페이스로 치환되지만, 뉴스타파 명세 등 원본은 쉼표 뒤가
            # 개행일 수 있어 모든 whitespace를 건너뛴다.
            k = i + 1
            while k < len(s) and s[k].isspace():
                k += 1
            nxt = s[k] if k < len(s) else ""
            had_ws = k > i + 1
            starts_item = re.match(r"[가-힣A-Za-z(]", nxt) is not None
            ends_item = cur.endswith("주") or cur.endswith(")")
            # depth==0이고 쉼표+공백이면 정상 구분자.
            cond_space = depth == 0 and had_ws
            # 앞이 'N주'/')'로 끝나고 뒤가 종목명 시작이면(공백 유무·depth 무관) 복구 분리.
            # → 닫히지 않은 괄호로 depth가 막힌 경우에도 항목이 뭉치지 않는다.
            cond_recover = ends_item and starts_item
            if cond_space or cond_recover:
                items.append(cur.strip())
                buf = []
                depth = 0
                i = k          # 쉼표와 뒤따르는 공백들을 건너뛴다
                continue
        buf.append(c)
        i += 1
    if buf:
        items.append("".join(buf).strip())
    return [x for x in items if x]


_TRANSLIT = [
    ("에스케이", "SK"), ("엘지", "LG"), ("케이티앤지", "KT&G"), ("케이티", "KT"),
    ("지에스", "GS"), ("씨제이", "CJ"), ("에이치디현대", "HD현대"), ("에이치디", "HD"),
    ("엘엑스", "LX"), ("엘에스", "LS"), ("케이비", "KB"), ("에스에스", "SS"), ("디엘", "DL"),
    ("에이치엘비", "HLB"), ("비지에프", "BGF"), ("케이씨씨", "KCC"), ("오씨아이", "OCI"),
    ("에이치엠엠", "HMM"),
]

# 관보 정식명·변형 표기 → KRX 약칭(사전에 실제 존재하는 이름). 공백 제거된 형태를 키로 한다.
# 관보는 정식명(중소기업은행/한화생명보험/신한금융지주회사…)을, KRX는 약칭(기업은행/한화생명/신한지주…)을
# 쓰기 때문에 매칭이 안 돼 국내주가 '해외·기타'로 빠지던 것을 바로잡는다. (모두 KRX 사전 존재 확인됨)
_KR_ALIAS = {
    "중소기업은행": "기업은행",
    "한화생명보험": "한화생명", "삼성생명보험": "삼성생명",
    "동양생명보험": "동양생명", "미래에셋생명보험": "미래에셋생명",
    "신한금융지주회사": "신한지주", "하나금융지주회사": "하나금융지주", "우리금융지주회사": "우리금융지주",
    "KB금융지주": "KB금융", "한국항공우주산업": "한국항공우주", "CJ씨지브이": "CJCGV",
    "현대바이오사이언스": "현대바이오", "KH필룩스": "필룩스",
    "현대두산인프라코어": "두산인프라코어", "HD현대인프라코어": "두산인프라코어",
    "맥쿼리한국인프라투융자회사": "맥쿼리인프라",
    "제이와이피엔터테인먼트": "JYPEnt.", "제이와이피엔터": "JYPEnt.",
    "한화인더스트리얼솔루션즈": "한화비전",   # 2024 사명 변경(코스피 489790)
    "일진다이아몬드": "일진다이아",
}

# 해외종목 표기 통합: 한글 음차 / 영문 정식명 / 한글 변형을 하나의 대표명으로.
# 상위·빈출 종목 위주(전량 통합은 아님). {대표명: [변형 표기들]}
_OVERSEAS = {
    "엔비디아": ["엔비디아", "NVIDIA", "NVIDIACORP", "NVIDIACORPORATION", "NVIDIAORD"],
    "테슬라": ["테슬라", "TESLA", "TESLAINC", "TESLAORD"],
    "애플": ["애플", "APPLE", "APPLEINC", "APPLEORD"],
    "마이크로소프트": ["마이크로소프트", "MICROSOFT", "MICROSOFTCORP", "MICROSOFTCORPORATION"],
    "아마존": ["아마존", "아마존닷컴", "AMAZON", "AMAZONCOM", "AMAZONCOMINC"],
    "알파벳A": ["알파벳A", "알파벳A주", "알파벳CLASSA", "알파벳클래스A", "ALPHABETINC", "ALPHABETINCCLASSA",
              "ALPHABETCLASSA", "ALPHABETA", "ALPHABETAORD", "ALPHABETINCCLASSAORD", "ALPHABETCLAORD",
              "ALPHABET"],
    "알파벳C": ["알파벳C", "알파벳C주", "알파벳CLASSC", "ALPHABETINCCLASSC", "ALPHABETC", "ALPHABETCORD",
              "ALPHABETCLCORD", "ALPHABETCLASSCORD"],
    "브로드컴": ["브로드컴", "BROADCOM", "BROADCOMINC"],
    "아이온큐": ["아이온큐", "IONQ", "IONQINC", "IONQORD"],
    "팔란티어": ["팔란티어", "팔란티어테크", "팔란티어테크놀로지스", "PALANTIR",
              "PALANTIRTECHNOLOGIES", "PALANTIRTECHNOLOGIESINC", "PALANTIRTECHINC",
              "PALANTIRTECHNOLOGIESCLAORD", "PALANTIRTECHNOLOGIESCLASSA", "PALANTIRTECHNOLOGIESA"],
    "코카콜라": ["코카콜라", "COCACOLA", "COCACOLACO", "THECOCACOLACOMPANY", "COCA-COLACO"],
    "뉴스케일파워": ["뉴스케일파워", "NUSCALE", "NUSCALEPOWER", "NUSCALEPOWERCORP",
                "NUSCALEPOWERCORPORATION", "NUSCALEPOWERCLAORD"],
    "리게티컴퓨팅": ["리게티", "리게티컴퓨팅", "RIGETTI", "RIGETTICOMPUTING", "RIGETTICOMPUTINGINC"],
    "AMD": ["AMD", "ADVANCEDMICRODEVICES", "ADVANCEDMICRODEVICESINC"],
    "메타": ["메타", "메타플랫폼스", "META", "METAPLATFORMS", "METAPLATFORMSINC"],
    "마이크론": ["마이크론", "마이크론테크놀로지", "MICRON", "MICRONTECHNOLOGY"],
    "인텔": ["인텔", "INTEL", "INTELCORP", "INTELCORPORATION"],
    "쿠팡": ["쿠팡", "COUPANG", "COUPANGINC"],
    "로켓랩": ["로켓랩", "ROCKETLAB", "ROCKETLABUSA", "ROCKETLABCORPORATION", "ROCKETLABORD", "RKLB"],
    "리얼티인컴": ["리얼티인컴", "REALTYINCOME", "REALTYINCOMECORP"],
    # 아래는 이번에 중복 통합한 종목들 (한글 음차 / 영문 티커 / 철자 변형 병합)
    "나이키": ["나이키", "나이키B", "NIKE", "NIKEINC", "NIKEINCCLASSB", "NIKEB", "NIKECLASSB"],
    "TSMC": ["TSMC", "TSMCADR", "TAIWANSEMICONDUCTOR", "TAIWANSEMICONDUCTORMANUFACTURING",
             "TAIWANSEMICONDUCTORMANUFACTURINGSPON", "TAIWANSEMICONDUCTORMANUFACTURINGSPONADR"],
    "아이렌": ["아이렌", "IREN", "IRENLIMITED", "IRENLTD"],
    "조비에비에이션": ["조비에비에이션", "조비", "JOBYAVIATION", "JOBYAVIATIONINC",
                 "JOBYAVIATIONCLAORD", "JOBYAVIATIONORD"],
    "스트래티지": ["스트래티지", "마이크로스트래티지", "MICROSTRATEGY", "MICROSTRATEGYINC",
              "STRATEGY", "STRATEGYINC", "MSTR"],
    "비트마인": ["비트마인", "비트마인이머션테크놀로지스", "BITMINE", "BITMINEIMMERSION",
             "BITMINEIMMERSIONTECHNOLOGIES", "BITMNEIMRSNORD"],
    "리커전파마슈티컬스": ["리커전파머슈티컬스", "리커젼파마슈티컬스", "리커전파마슈티컬스",
                  "RECURSION", "RECURSIONPHARMACEUTICALS"],
    "로빈후드": ["로빈후드", "로빈훗마케츠", "ROBINHOOD", "ROBINHOODMARKETS", "ROBINHOODMARKETSINC"],
    "유나이티드헬스그룹": ["유나이티드헬스그룹", "UNITEDHEALTHGROUP", "UNITEDHEALTHGROUPINC",
                  "UNITEDHEALTHGRPORD"],
    # 티커 확정·표기 통합 배치(2026-07 데이터 점검)
    "실SQ": ["실SQ", "SEALSQ", "SEALSQCORP"],
    "소파이테크놀로지스": ["소파이테크놀로지스", "소파이", "SOFITECHNOLOGIES", "SOFITECHNOLOGIESINC"],
    "코스트코홀세일": ["코스트코홀세일", "코스트코", "COSTCO", "COSTCOWHOLESALE", "COSTCOWHOLESALECORP"],
    "서클인터넷그룹": ["서클인터넷그룹", "써클인터넷그룹", "CIRCLEINTERNETGROUP", "CIRCLEINTERNETGROUPINC"],
    "ASML": ["ASML", "ASML홀딩", "ASMLHOLDING", "ASMLHOLDINGNV"],
    "유니티소프트웨어": ["유니티소프트웨어", "유니티", "UNITYSOFTWARE", "UNITYSOFTWAREINC"],
    "뱅크오브아메리카": ["뱅크오브아메리카", "BANKOFAMERICA", "BANKOFAMERICACORP", "BANKOFAMERICACORPORATION"],
    "비트팜스": ["비트팜스", "BITFARMS", "킬인프라스트럭처", "킬인프라스트럭쳐", "KIELINFRASTRUCTURE"],
    "SESAI": ["SESAI", "SESAICORP"],
}
# 폴딩 조회표: 대문자·공백제거 변형 → 대표명
_OVERSEAS_FOLD = {}
for _canon, _vs in _OVERSEAS.items():
    for _v in _vs:
        _OVERSEAS_FOLD[_v.replace(" ", "").upper()] = _canon

# 해외 대표명 → 야후 티커 (시세·비중 계산용)
OVERSEAS_TICKER = {
    "엔비디아": "NVDA", "테슬라": "TSLA", "애플": "AAPL", "마이크로소프트": "MSFT",
    "아마존": "AMZN", "알파벳A": "GOOGL", "알파벳C": "GOOG", "브로드컴": "AVGO",
    "아이온큐": "IONQ", "팔란티어": "PLTR", "코카콜라": "KO", "뉴스케일파워": "SMR",
    "리게티컴퓨팅": "RGTI", "AMD": "AMD", "메타": "META", "마이크론": "MU",
    "나이키": "NKE", "TSMC": "TSM", "아이렌": "IREN", "조비에비에이션": "JOBY",
    "스트래티지": "MSTR", "비트마인": "BMNR", "리커전파마슈티컬스": "RXRX",
    "로빈후드": "HOOD", "유나이티드헬스그룹": "UNH",
    "인텔": "INTC", "쿠팡": "CPNG", "로켓랩": "RKLB", "리얼티인컴": "O",
    "실SQ": "LAES", "소파이테크놀로지스": "SOFI", "코스트코홀세일": "COST",
    "서클인터넷그룹": "CRCL", "ASML": "ASML", "DirexionDailyPLTRBull2XShares": "PLTU",
    "USA레어어스": "USAR", "유니티소프트웨어": "U", "뱅크오브아메리카": "BAC",
    "머크": "MRK", "인튜이티브서지컬": "ISRG", "AST스페이스모바일": "ASTS",
    "테라울프": "WULF", "비트팜스": "KEEL", "SESAI": "SES", "리졸브AI": "RZLV",
    "크리티컬메탈스": "CRML", "비욘드미트": "BYND",
    # 상위 해외종목 티커 보강 (웹 표기 + 시세 조회용)
    "넷플릭스": "NFLX", "오라클": "ORCL", "스타벅스": "SBUX", "화이자": "PFE", "오클로": "OKLO",
    "존슨앤드존슨": "JNJ", "템퍼스AI": "TEM", "월트디즈니": "DIS", "월마트": "WMT",
    "서클인터넷그룹": "CRCL", "플러그파워": "PLUG", "솔리드파워": "SLDP", "퀀텀컴퓨팅": "QUBT",
    "로블록스": "RBLX", "코인베이스글로벌": "COIN", "아처에비에이션": "ACHR", "노보노디스크": "NVO",
    "MP머티리얼스": "MP", "엑슨모빌": "XOM", "버크셔해서웨이B": "BRK-B", "비자": "V",
    "루멘텀홀딩스": "LITE", "앱러빈": "APP", "시놉시스": "SNPS", "일라이릴리": "LLY",
    "루시드그룹": "LCID", "사운드하운드AI": "SOUN", "버티브홀딩스": "VRT", "퀄컴": "QCOM",
    "록히드마틴": "LMT", "셰브론": "CVX", "제이피모간체이스": "JPM", "포드모터": "F",
    "아리스타네트웍스": "ANET", "모더나": "MRNA", "뉴몬트": "NEM", "램리서치": "LRCX",
    "셀레스티카": "CLS", "보잉": "BA", "센트러스에너지": "LEU", "워너브로스디스커버리": "WBD",
    "스노우플레이크": "SNOW", "빅베어AI홀딩스": "BBAI", "블룸에너지": "BE", "네비우스그룹": "NBIS",
    "디웨이브퀀텀": "QBTS", "서브로보틱스": "SERV", "인튜이티브머신스": "LUNR", "마이크로스트래티지": "MSTR",
    # 티커성 표기(영문 원문) → 정식 티커
    "AT&T": "T", "IBM": "IBM", "InvescoQQQTrustSeries1": "QQQ",
    "SPDRGoldShares": "GLD", "SPDRS&P500ETFTrust": "SPY",
}


def normalize(name):
    """KRX 사전 매칭용 정규화.
    - 우선주 표기 변형(삼성전자1우선주/우선주 → 우) 통일
    - 한글 음차 약칭(에스케이하이닉스 → SK하이닉스) 통일
    - 우선주 접미사 '우'는 남겨 보통주와 구분"""
    import unicodedata
    n = (name or "").strip()
    n = unicodedata.normalize("NFKC", n)   # 전각→반각 통일: 'ＫＢ금융'→'KB금융', '１'→'1' 등
    n = re.sub(r"^\(주\)", "", n)
    n = re.sub(r"\(주\)$", "", n)
    n = n.replace("주식회사", "")
    n = re.sub(r"보통주$", "", n)          # '...보통주' → '...'
    n = re.sub(r"(\d*)우선주\(신형\)$", lambda m: (m.group(1) or "") + "우B", n)  # '2우선주(신형)'→'2우B'
    n = re.sub(r"\d*우선주$", "우", n)      # '삼성전자1우선주' → '삼성전자우'
    n = re.sub(r"\s+", "", n)              # 일반 공백 + NBSP 등 유니코드 공백 전부 제거 ('K B금융'→'KB금융')
    if n.startswith("현대자동차"):          # '현대자동차우'→'현대차우', '현대자동차2우B'→'현대차2우B'
        n = "현대차" + n[len("현대자동차"):]
    # 관보 정식명·변형 → KRX 약칭 (중소기업은행→기업은행 등)
    n = _KR_ALIAS.get(n, n)
    # 한글 음차 → 영문 약칭 (KRX 정식명이 영문인 경우 정렬)
    for ko, en in _TRANSLIT:
        if n.startswith(ko):
            n = en + n[len(ko):]
            break
    # 해외종목 표기 통합 (엔비디아/NVIDIACORP 등 → 대표명)
    folded = _OVERSEAS_FOLD.get(n.upper())
    if not folded:
        # 뒤에 붙은 괄호주석 제거 후 재시도: 메타플랫폼스(페이스북)→메타, TSMC(ADR)→TSMC (같은 회사 통합)
        stripped = re.sub(r"\([^()]*\)$", "", n)
        if stripped != n:
            folded = _OVERSEAS_FOLD.get(stripped.upper())
    if not folded and "." in n:
        # 마침표 제거 후 재시도: Amazon.com→아마존, SPOTIFYTECHNOLOGYS.A.→... 등
        folded = _OVERSEAS_FOLD.get(n.replace(".", "").upper())
    if folded:
        return folded
    return n


def _variants(norm):
    """정식명↔약명 표기 변형 후보 생성 (양방향 매칭 시도용)."""
    cands = {norm}
    # 접미사 축약: 한국전력공사→한국전력, ...공사/...보험/...금융지주 등
    for suf in ("공사", "주식회사", "홀딩스"):
        if norm.endswith(suf) and len(norm) > len(suf) + 1:
            cands.add(norm[: -len(suf)])
    # 자동차↔차
    if norm.endswith("자동차"):
        cands.add(norm[:-3] + "차")
    if norm.endswith("차") and not norm.endswith("자동차"):
        cands.add(norm[:-1] + "자동차")
    # 역방향: 축약형에 공사 붙이기
    cands.add(norm + "공사")
    return cands


def classify(name):
    norm = normalize(name)
    for cand in _variants(norm):
        if cand in KRX_LISTED:
            return "국내상장", KRX_LISTED[cand]
    # 사전에 없고, 한글이 전혀 없이 영문/숫자로만 된 표기 → 해외 추정 (v2 대상)
    if re.search(r"[A-Za-z]", name) and not re.search(r"[가-힣]", name):
        return "해외추정", None
    return "미매칭", None


def parse_item(item: str) -> Holding:
    raw = item
    change, direction = None, None

    m = CHANGE_RE.search(item)
    if m:
        c = _num(m.group(1))
        if c is not None:
            change = c
            direction = m.group(2)
            item = item[: m.start()].strip()

    qty, name = None, item
    m2 = QTY_RE.search(item)
    if m2:
        q = _num(m2.group(1))
        if q is not None:
            qty = q
            name = item[: m2.start()].strip()

    # 안 닫힌 괄호 꼬리 제거: '메타플랫폼스(페이스' → '메타플랫폼스'
    # (닫힌 괄호 '삼성전자(우선주)'나 '샤오펑(ADR)'은 유지)
    if name.count("(") > name.count(")"):
        name = re.sub(r"\([^)]*$", "", name).strip()

    cat, code = classify(name)
    return Holding(
        raw=raw,
        name=name,
        qty=qty,
        change=change,
        direction=direction,
        sold_out=(qty == 0),
        krx_code=code,
        category=cat,
    )


def parse_securities(text: str) -> list[Holding]:
    return [parse_item(it) for it in split_items(text)]


# ---------------------------------------------------------------------------
# 데모 실행
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 샘플 A: 사용자가 준 증권 텍스트(국내 매도분 + 미국주식 한글/영문 혼재)
    block_a = (
        "뉴파워프라즈마 0주(1,601주 감소), 제이아이테크 0주(2,297주 감소), "
        "하이스틸 0주(1,947주 감소), AMD 16주, 메타플랫폼스(페이스북) 14주, "
        "브로드컴 15주(15주 증가), 아마존닷컴 0주(56주 감소), 애플 19주, "
        "엔비디아 115주(105주 증가), 테슬라 10주(10주 증가), "
        "ASMLHOLDINGNVEUR0.09NYREGISTRYSHS 6주, ATLASSIANCORP 6주, NIOINC 2주, "
        "NVIDIACORP 30주(27주 증가), SPOTIFYTECHNOLOGYS.A. 4주, "
        "TESLAINC 4주(4주 증가), UNITYSOFTWAREINC 10주, "
        "맥쿼리한국인프라투융자회사보통주 1,340주(1,340주 증가)"
    )

    # 샘플 B: 안철수 PDF의 '상장주식' 행 + 예금 행(주 없이 금액만) — 견고성 확인용
    block_b_listed = "안랩 1,860,000주"
    block_b_deposit = (
        "(주)KEB하나은행 186,934(197,972 감소), 부산은행 5,708(25,829 감소), "
        "삼성화재해상보험 809(335 증가)"
    )

    def show(title, text):
        print("=" * 78)
        print(title)
        print("=" * 78)
        rows = parse_securities(text)
        header = f"{'종목명':<26}{'수량':>10}{'증감':>10}{'방향':>6}{'분류':>8} {'코드'}"
        print(header)
        print("-" * 78)
        for h in rows:
            name = (h.name[:24] + "…") if len(h.name) > 25 else h.name
            qty = f"{h.qty:,}" if h.qty is not None else "-"
            chg = f"{h.change:,}" if h.change is not None else "-"
            dirn = h.direction or "-"
            code = h.krx_code or ""
            flag = "  ← 전량매도" if h.sold_out else ""
            print(f"{name:<26}{qty:>10}{chg:>10}{dirn:>6}{h.category:>8} {code}{flag}")
        return rows

    a = show("[샘플 A] 증권 텍스트", block_a)
    print()
    b1 = show("[샘플 B-1] 안철수 상장주식", block_b_listed)
    print()
    b2 = show("[샘플 B-2] 예금(주 없는 금액형)", block_b_deposit)

    # 집계 데모: '보유 중 국내주식'만 순위에 올린다 (0주 제외)
    print()
    print("=" * 78)
    print("[집계 데모] 국내주식 순위 후보 (전량매도 제외, KRX 매칭만)")
    print("=" * 78)
    all_rows = a + b1 + b2
    domestic = [h for h in all_rows if h.category == "국내상장" and not h.sold_out]
    sold = [h for h in all_rows if h.category == "국내상장" and h.sold_out]
    overseas = [h for h in all_rows if h.category == "해외추정"]

    print(f"보유 중 국내주식 {len(domestic)}건 → 순위 테이블 진입")
    for h in domestic:
        print(f"   · {h.name} ({h.krx_code}) {h.qty:,}주")
    print(f"\n전량매도(국내) {len(sold)}건 → 'v2: 공직자가 던진 종목' 피처로 보관")
    for h in sold:
        print(f"   · {h.name} (직전 대비 {h.change:,}주 {h.direction})")
    print(f"\n해외추정 {len(overseas)}건 → v2로 이월 (한글/영문 표기 통합 필요)")
    print("   예: 애플 / 엔비디아 / TESLAINC / NVIDIACORP …")
