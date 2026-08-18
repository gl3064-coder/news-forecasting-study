from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ..db import get_connection
from .rss import build_summary, clean_text


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
STYLE_BLOCK_RE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)
SCRIPT_BLOCK_RE = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
MSO_RE = re.compile(r"#outlook a\{.*?\}|@media[^{]*\{[\s\S]*?\}", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+")
MULTI_NEWLINE_RE = re.compile(r"\n{2,}")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
DATE_STAMP_RE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},\s+\d{4}(,\s+\d{1,2}:\d{2}\s*(am|pm)\s*(eastern|central|mountain|pacific)?\s*time)?",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\s*(a\.m\.|p\.m\.|am|pm)\b", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b(?:www\.)?[a-z0-9-]+\.(?:com|org|net|edu)\b", re.IGNORECASE)
PROOFPOINT_RE = re.compile(r"https?://\S*urldefense\S+", re.IGNORECASE)
PROOFPOINT_WRAPPED_RE = re.compile(r"https?://\s*urldefense\.\s+/?\S+", re.IGNORECASE)
PROOFPOINT_FRAGMENT_RE = re.compile(r"\S*&d=Dw[A-Za-z0-9_-]+&[cmrse]=\S*", re.IGNORECASE)
BYLINE_RE = re.compile(r"\bby\s+[A-Z][A-Za-z.\- ]+(?=$|[.!?])")
NEWSLETTER_NOISE_PATTERNS = [
    re.compile(r"view in browser", re.IGNORECASE),
    re.compile(r"is this email difficult to read\??", re.IGNORECASE),
    re.compile(r"manage preferences", re.IGNORECASE),
    re.compile(r"unsubscribe", re.IGNORECASE),
    re.compile(r"advertisement", re.IGNORECASE),
    re.compile(r"sign up here", re.IGNORECASE),
    re.compile(r"share full article", re.IGNORECASE),
    re.compile(r"gift article", re.IGNORECASE),
    re.compile(r"listen to this article", re.IGNORECASE),
    re.compile(r"your guide to the wsj\S* exclusive reporting and analysis", re.IGNORECASE),
    re.compile(r"you received this email because", re.IGNORECASE),
    re.compile(r"subscribe to the times", re.IGNORECASE),
    re.compile(r"get the new york times app", re.IGNORECASE),
    re.compile(r"connect with us on:", re.IGNORECASE),
    re.compile(r"change your email", re.IGNORECASE),
    re.compile(r"privacy policy", re.IGNORECASE),
    re.compile(r"contact us", re.IGNORECASE),
    re.compile(r"california notices", re.IGNORECASE),
    re.compile(r"hello there", re.IGNORECASE),
    re.compile(r"the new york times for you", re.IGNORECASE),
    re.compile(r"nyt wirecutter", re.IGNORECASE),
    re.compile(r"the recommendation", re.IGNORECASE),
    re.compile(r"this newsletter includes coverage you might be interested in, based on what you've read", re.IGNORECASE),
    re.compile(r"it might also include stories that are local to you", re.IGNORECASE),
    re.compile(r"good morning", re.IGNORECASE),
    re.compile(r"sponsored by", re.IGNORECASE),
]
TIER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "geopolitical": (
        r"war|wars|warfare|wartime",
        r"cease-?fires?|armistice",
        r"invasions?|invade[ds]?|invading",
        r"air ?strikes?|bombard\w+|shelling",
        r"missiles?|warheads?",
        r"troops?|soldiers?",
        r"militar(?:y|ies)|militias?",
        r"militants?|insurgen\w+",
        r"terroris(?:m|ts?)",
        r"nato",
        r"kremlin",
        r"pentagon",
        r"iran|iranians?",
        r"israel|israelis?",
        r"gaza|west bank",
        r"hamas",
        r"hezbollah",
        r"houthis?",
        r"ukraine|ukrainians?",
        r"russia|russians?",
        r"putin",
        r"china|chinese",
        r"beijing",
        r"taiwan",
        r"north korea|pyongyang",
        r"venezuela|venezuelans?",
        r"maduro",
        r"tehran",
        r"strait of hormuz",
        r"sanctions?|sanctioned|sanctioning",
        r"embargo(?:es)?",
        r"tariffs?",
        r"diplomats?|diplomatic|diplomacy",
        r"treat(?:y|ies)",
        r"ambassadors?",
        r"geopolitical|geopolitics",
        r"refugees?|asylum",
        r"coup",
        r"regimes?",
        r"elections?|electoral",
        r"voters?|ballots?",
        r"trump",
        r"biden",
        r"netanyahu",
        r"zelensky",
        r"xi jinping",
        r"white house",
        r"congress|congressional",
        r"senate|senators?",
        r"parliament|parliamentary",
        r"prime ministers?",
        r"foreign polic(?:y|ies)",
        r"nuclear",
        r"immigration|immigrants?|deportations?",
    ),
    "finance": (
        r"markets?",
        r"stocks?|shares",
        r"equit(?:y|ies)",
        r"bonds?",
        r"yields?",
        r"treasur(?:y|ies)",
        r"federal reserve|the fed|fed's|fed chair|fomc",
        r"powell|central banks?",
        r"interest rates?|rate cuts?|rate hikes?",
        r"inflation|inflationary|deflation",
        r"cpi",
        r"recessions?",
        r"gdp",
        r"unemployment|jobless|payrolls?",
        r"earnings",
        r"revenues?",
        r"profits?|profitability",
        r"ipos?",
        r"mergers?|acquisitions?|takeovers?",
        r"buyouts?",
        r"private equity",
        r"private credit",
        r"hedge funds?",
        r"venture capital",
        r"investors?|investments?|investing",
        r"portfolios?",
        r"s&p 500|s&p",
        r"nasdaq",
        r"dow jones|the dow",
        r"etfs?",
        r"dividends?",
        r"valuations?",
        r"sell-?offs?",
        r"bull market|bear market",
        r"crude|opec|oil prices?|barrels? of oil",
        r"commodit(?:y|ies)",
        r"currenc(?:y|ies)|the dollar",
        r"bitcoin|crypto|cryptocurrenc(?:y|ies)",
        r"banks?|banking",
        r"lending|lenders?|loans?",
        r"credit",
        r"debts?|deficits?",
        r"defaults?",
        r"bankrupt|bankruptc(?:y|ies)",
        r"layoffs?",
        r"wall street",
        r"shareholders?",
        r"mortgages?",
        r"housing market",
        r"retail sales|consumer spending",
        r"traders?|trading",
        r"econom(?:y|ic|ics|ist)",
    ),
    "lifestyle": (
        r"wirecutter",
        r"recipes?",
        r"cook|cooks|cooked|cooking|chefs?",
        r"restaurants?|dining",
        r"menus?",
        r"desserts?|pastr(?:y|ies)|baker(?:y|ies)",
        r"cocktails?|wines?|brunch",
        r"tast(?:e|ed|es|ing)|flavors?|delicious",
        r"styles?|stylish|fashion|wardrobes?|outfits?",
        r"sneakers?|shoes?",
        r"skincare|makeup|mascara|beauty",
        r"sports?|athletes?",
        r"nfl|nba|mlb|nhl|soccer|tennis|golf|olympics?|world cup",
        r"workouts?|fitness|gyms?|yoga|exercise",
        r"wellness|longevity|nutrition|diets?",
        r"sleep|mattress(?:es)?|pillows?|sheets",
        r"vacuums?|air purifiers?|appliances?",
        r"gifts?",
        r"travel|traveling|hotels?|vacations?|luggage",
        r"museums?|galler(?:y|ies)",
        r"movies?|films?|tv shows?",
        r"albums?|concerts?",
        r"celebrit(?:y|ies)",
        r"novels?|memoirs?|book review",
        r"puzzles?|crossword|wordle",
        r"parenting|toddlers?",
        r"pets?|dogs?",
        r"gardens?|gardening",
        r"shopping|shoppers?",
        r"kitchens?|furniture",
        r"weddings?|dating|romance",
        r"clothes|clothing|garments?|laundry",
        r"relationships?|marriage|divorce|therapy|friendships?",
    ),
}


def compile_tier_pattern(keywords: tuple[str, ...]) -> re.Pattern[str]:
    """One pattern per tier. Each keyword becomes its own named group so a
    concept counts once no matter which inflection matched."""
    parts = [f"(?P<k{index}>{keyword})" for index, keyword in enumerate(keywords)]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


TIER_PATTERNS = {tier: compile_tier_pattern(kws) for tier, kws in TIER_KEYWORDS.items()}
TIER_LEAD_CHARS = 300
TIER_SUBJECT_WEIGHT = 3
TIER_LEAD_WEIGHT = 2
TIER_BODY_WEIGHT = 1
TIER_BODY_CAP = 3
TIER_TIE_ORDER = ("finance", "geopolitical", "lifestyle")
# How far the winning tier must beat the runner-up before we commit to it.
# Graded on the 2,049-message corpus: 0 -> 78% accurate but "mixed" never fires,
# 1 -> 82% and finance holds 79% recall, 2 -> 85% but finance recall drops to 74%.
# 1 is the compromise, since losing finance stories to "mixed" is the failure
# this classifier exists to prevent. Raise to 2 to favor precision instead.
TIER_MIN_MARGIN = 1

HIGH_SIGNAL_SUBJECT_PATTERNS = [
    re.compile(r"^the morning:", re.IGNORECASE),
    re.compile(r"^the world:", re.IGNORECASE),
    re.compile(r"dealbook", re.IGNORECASE),
    re.compile(r"markets", re.IGNORECASE),
    re.compile(r"what's news", re.IGNORECASE),
    re.compile(r"heard on the street", re.IGNORECASE),
    re.compile(r"capital journal", re.IGNORECASE),
]
LOW_SIGNAL_SUBJECT_PATTERNS = [
    re.compile(r"^for you:", re.IGNORECASE),
    re.compile(r"wirecutter", re.IGNORECASE),
    re.compile(r"^n\.y\. today:", re.IGNORECASE),
    re.compile(r"^the 10-point:", re.IGNORECASE),
    re.compile(r"style", re.IGNORECASE),
    re.compile(r"sports", re.IGNORECASE),
    re.compile(r"welcome to", re.IGNORECASE),
    re.compile(r"get started", re.IGNORECASE),
    re.compile(r"your subscription", re.IGNORECASE),
    re.compile(r"verify your", re.IGNORECASE),
    re.compile(r"confirm your", re.IGNORECASE),
    re.compile(r"^10 point", re.IGNORECASE),
    re.compile(r"^the 10 point", re.IGNORECASE),
    re.compile(r"cancer", re.IGNORECASE),
    re.compile(r"eating habits", re.IGNORECASE),
    re.compile(r"health tip", re.IGNORECASE),
    re.compile(r"well newsletter", re.IGNORECASE),
    re.compile(r"cooking", re.IGNORECASE),
    re.compile(r"fitness", re.IGNORECASE),
    re.compile(r"diet", re.IGNORECASE),
    re.compile(r"wellness", re.IGNORECASE),
]


def credentials_file() -> Path:
    return Path(os.getenv("GMAIL_CREDENTIALS_FILE", "backend/credentials.json"))


def token_file() -> Path:
    return Path(os.getenv("GMAIL_TOKEN_FILE", "backend/token.json"))


def allowed_domains() -> list[str]:
    raw = os.getenv("PULSE_GMAIL_ALLOWED_DOMAINS", "nytimes.com,wsj.com")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def target_label() -> str:
    return os.getenv("PULSE_GMAIL_LABEL", "Pulse")


def max_results() -> int:
    try:
        return int(os.getenv("PULSE_GMAIL_MAX_RESULTS", "100"))
    except ValueError:
        return 25


def get_gmail_credentials() -> Credentials:
    creds: Credentials | None = None
    token_path = token_file()
    credentials_path = credentials_file()

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not credentials_path.exists():
            raise FileNotFoundError(
                f"Gmail credentials file not found at {credentials_path}. "
                "Download the OAuth desktop app JSON and place it there."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def build_gmail_service():
    creds = get_gmail_credentials()
    return build("gmail", "v1", credentials=creds)


def sender_allowed(sender: str) -> bool:
    lowered = sender.lower()
    return any(domain in lowered for domain in allowed_domains())


def find_label_id(service, label_name: str) -> str | None:
    response = service.users().labels().list(userId="me").execute()
    labels = response.get("labels", [])
    for label in labels:
        if label.get("name", "").lower() == label_name.lower():
            return label.get("id")
    return None


def detect_source(sender: str, subject: str) -> tuple[str, str]:
    lowered = f"{sender} {subject}".lower()
    if "wsj" in lowered or "wall street journal" in lowered:
        return "WSJ Newsletter", "WSJ"
    return "NYT Newsletter", "NYT"


def tier_concepts(pattern: re.Pattern[str], text: str) -> set[str]:
    """Distinct concepts the pattern finds, not raw hit count. Ten mentions of
    "Trump" are one concept, so a single repeated word can't carry a tier."""
    return {match.lastgroup for match in pattern.finditer(text) if match.lastgroup}


def tier_scores(subject: str, content: str) -> dict[str, int]:
    """Score every tier by where its keywords land. The subject is the strongest
    evidence, the opening of the newsletter is next, and the rest of the body is
    capped so a 5,000-character footer can't outvote the headline."""
    subject = subject or ""
    body = remove_subject_echo(subject, (content or "").lstrip())
    lead, rest = body[:TIER_LEAD_CHARS], body[TIER_LEAD_CHARS:]

    scores: dict[str, int] = {}
    for tier, pattern in TIER_PATTERNS.items():
        in_subject = tier_concepts(pattern, subject)
        in_lead = tier_concepts(pattern, lead) - in_subject
        in_rest = tier_concepts(pattern, rest) - in_subject - in_lead
        scores[tier] = (
            TIER_SUBJECT_WEIGHT * len(in_subject)
            + TIER_LEAD_WEIGHT * len(in_lead)
            + TIER_BODY_WEIGHT * min(len(in_rest), TIER_BODY_CAP)
        )
    return scores


def detect_tier(subject: str, content: str) -> str:
    """Classify a newsletter as geopolitical / finance / lifestyle / mixed.

    Keywords match on word boundaries with explicit inflections, so "forwarded"
    is not a war story and "sanctions" still counts as one. Every tier is scored
    and the winner takes it, but only if it clears the runner-up by
    TIER_MIN_MARGIN. Newsletters that trip no keyword, or that are a genuine
    grab bag with no dominant theme, come back as mixed.
    """
    scores = tier_scores(subject, content)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], TIER_TIE_ORDER.index(item[0])))
    (best_tier, best_score), (_, runner_up) = ranked[0], ranked[1]
    if best_score == 0 or best_score - runner_up < TIER_MIN_MARGIN:
        return "mixed"
    return best_tier


def newsletter_signal(subject: str, content: str) -> str:
    if any(pattern.search(subject) for pattern in LOW_SIGNAL_SUBJECT_PATTERNS):
        return "low"
    if any(pattern.search(subject) for pattern in HIGH_SIGNAL_SUBJECT_PATTERNS):
        return "high"

    text = f"{subject} {content}".lower()
    high_keywords = [
        "fed",
        "inflation",
        "rates",
        "economy",
        "markets",
        "earnings",
        "stocks",
        "oil",
        "war",
        "iran",
        "china",
        "tariff",
        "sanction",
        "treasury",
        "yield",
    ]
    low_keywords = ["running gear", "wirecutter", "recipes", "restaurant", "shopping", "gift guide"]
    if any(token in text for token in low_keywords):
        return "low"
    if any(token in text for token in high_keywords):
        return "high"
    return "medium"


def parse_received_at(headers: list[dict[str, str]]) -> str:
    raw_date = next((item["value"] for item in headers if item["name"].lower() == "date"), "")
    if not raw_date:
        return datetime.now(timezone.utc).isoformat()

    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError, IndexError):
        return datetime.now(timezone.utc).isoformat()

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def decode_body(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(f"{data}{padding}").decode("utf-8", errors="ignore")


def extract_parts(payload: dict[str, Any]) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if data:
            decoded = decode_body(data)
            if mime_type == "text/plain":
                plain_parts.append(decoded)
            elif mime_type == "text/html":
                html_parts.append(decoded)

        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return "\n".join(plain_parts).strip(), "\n".join(html_parts).strip()


def clean_newsletter_html(value: str) -> str:
    text = COMMENT_RE.sub(" ", value or "")
    text = STYLE_BLOCK_RE.sub(" ", text)
    text = SCRIPT_BLOCK_RE.sub(" ", text)
    text = MSO_RE.sub(" ", text)
    text = text.replace("</p>", "\n").replace("</div>", "\n").replace("</li>", "\n")
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    return clean_text(text)


def strip_inline_noise(text: str) -> str:
    cleaned = DATE_STAMP_RE.sub(" ", text)
    cleaned = TIME_RE.sub(" ", cleaned)
    cleaned = DOMAIN_RE.sub(" ", cleaned)
    cleaned = PROOFPOINT_RE.sub(" ", cleaned)
    cleaned = PROOFPOINT_WRAPPED_RE.sub(" ", cleaned)
    cleaned = PROOFPOINT_FRAGMENT_RE.sub(" ", cleaned)
    cleaned = BYLINE_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("Ad Ad", " ")
    cleaned = cleaned.replace("Ad ", " ")
    cleaned = cleaned.replace(" ad ", " ")
    for pattern in NEWSLETTER_NOISE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" -|,")


def looks_like_css_noise(value: str) -> bool:
    lowered = value.lower()
    signals = ["#outlook", "mso-table", "@media", "font-family:", "border-collapse"]
    return sum(signal in lowered for signal in signals) >= 2


def is_noise_line(line: str) -> bool:
    lowered = line.lower().strip()
    if not lowered:
        return True
    if URL_RE.fullmatch(lowered):
        return True
    if len(lowered) < 4:
        return True
    if lowered.isdigit():
        return True
    return any(pattern.search(lowered) for pattern in NEWSLETTER_NOISE_PATTERNS)


def normalize_newsletter_lines(text: str) -> list[str]:
    normalized = MULTI_NEWLINE_RE.sub("\n", text)
    lines: list[str] = []
    for raw_line in normalized.splitlines():
        line = strip_inline_noise(raw_line.strip())
        if is_noise_line(line):
            continue
        lines.append(line)
    return lines


def remove_subject_echo(subject: str, content: str) -> str:
    lowered_subject = subject.lower().strip()
    lowered_content = content.lower().strip()
    if lowered_subject and lowered_content.startswith(lowered_subject):
        return content[len(subject) :].lstrip(" :-|\n")
    return content


def clean_trailing_fragment(text: str) -> str:
    cleaned = text.strip()
    if cleaned.endswith(" of..."):
        cleaned = cleaned[:-5].rstrip()
    if cleaned.endswith(" ..."):
        cleaned = cleaned[:-4].rstrip()
    if cleaned.endswith(".."):
        cleaned = cleaned.rstrip(". ").rstrip()
    return cleaned


def normalize_for_compare(text: str) -> str:
    return re.sub(r"\W+", "", text.lower())


def trim_to_complete_sentences(text: str, char_limit: int, min_sentences: int = 1, max_sentences: int = 3) -> str:
    cleaned = clean_trailing_fragment(text)
    if len(cleaned) <= char_limit:
        return cleaned

    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(cleaned) if part.strip()]
    chosen: list[str] = []
    for sentence in sentences:
        candidate = " ".join(chosen + [sentence]).strip()
        if len(candidate) > char_limit and len(chosen) >= min_sentences:
            break
        if len(chosen) >= max_sentences:
            break
        chosen.append(sentence)

    if chosen:
        return clean_trailing_fragment(" ".join(chosen))

    clipped = cleaned[:char_limit]
    last_punct = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if last_punct > int(char_limit * 0.6):
        return clean_trailing_fragment(clipped[: last_punct + 1])

    last_space = clipped.rfind(" ")
    if last_space > 0:
        return clean_trailing_fragment(clipped[:last_space])
    return clean_trailing_fragment(clipped)


def build_description_from_content(content: str, summary: str, char_limit: int = 500) -> str:
    paragraphs = [part.strip() for part in content.split("\n") if part.strip()]
    if not paragraphs:
        return summary

    summary_key = normalize_for_compare(summary)
    remaining: list[str] = []
    for paragraph in paragraphs:
        paragraph_key = normalize_for_compare(paragraph)
        if paragraph_key and paragraph_key == summary_key:
            continue
        if paragraph_key and summary_key and paragraph_key in summary_key:
            continue
        if summary_key and paragraph_key and summary_key in paragraph_key and len(paragraph_key) < len(summary_key) + 40:
            continue
        remaining.append(paragraph)

    candidate = " ".join(remaining).strip()
    if not candidate:
        return summary

    return trim_to_complete_sentences(candidate, char_limit, min_sentences=1, max_sentences=3)


def extract_summary_and_description(content: str, subject: str) -> tuple[str, str]:
    flattened = strip_inline_noise(content.replace("\n", " "))
    flattened = remove_subject_echo(subject, flattened)
    flattened = clean_trailing_fragment(flattened)
    sentences = [strip_inline_noise(part.strip()) for part in SENTENCE_SPLIT_RE.split(flattened) if part.strip()]
    sentences = [sentence for sentence in sentences if sentence and len(sentence) > 20]

    if not sentences:
        summary = build_summary(subject, flattened or content)
        return summary, ""

    summary = trim_to_complete_sentences(sentences[0], 220, min_sentences=1, max_sentences=1)
    description_sentences: list[str] = []
    summary_key = normalize_for_compare(summary)
    for sentence in sentences[1:]:
        sentence_key = normalize_for_compare(sentence)
        if not sentence_key:
            continue
        if sentence_key == summary_key:
            continue
        if sentence_key in summary_key or summary_key in sentence_key:
            continue
        description_sentences.append(sentence)
        if len(description_sentences) >= 2:
            break

    description = ""
    if description_sentences:
        description = trim_to_complete_sentences(" ".join(description_sentences), 420, min_sentences=1, max_sentences=2)
    return summary, description


def extract_preview(lines: list[str], subject: str) -> tuple[str, str]:
    content = "\n".join(lines).strip()
    content = remove_subject_echo(subject, content)
    content = clean_trailing_fragment(content)
    if not content:
        return "No summary available.", ""

    paragraphs = [part.strip() for part in content.split("\n") if part.strip()]
    preview_parts: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) < 25 and not preview_parts:
            continue
        preview_parts.append(paragraph)
        if len(" ".join(preview_parts)) >= 280:
            break

    preview_text = " ".join(preview_parts).strip() or paragraphs[0]
    preview_text = strip_inline_noise(preview_text)
    preview_text = trim_to_complete_sentences(preview_text, 320, min_sentences=1, max_sentences=2)
    return preview_text, content


def clean_newsletter_content(plain_text: str, html_text: str, snippet: str) -> str:
    candidates = []
    if plain_text:
        candidates.append(clean_text(plain_text))
    if html_text:
        candidates.append(clean_newsletter_html(html_text))
    if snippet:
        candidates.append(clean_text(snippet))

    cleaned: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        if looks_like_css_noise(candidate):
            continue

        lines = normalize_newsletter_lines(candidate)
        if lines:
            cleaned.append("\n".join(lines))

    if cleaned:
        cleaned.sort(key=len, reverse=True)
        return cleaned[0]

    return clean_text(snippet)


def newsletter_link(message_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{message_id}"


def save_newsletter(record: dict[str, str]) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO newsletters (
                gmail_message_id,
                thread_id,
                subject,
                sender,
                received_at,
                link,
                source,
                source_icon,
                tier,
                summary,
                description,
                full_content,
                label_names
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gmail_message_id) DO UPDATE SET
                thread_id=excluded.thread_id,
                subject=excluded.subject,
                sender=excluded.sender,
                received_at=excluded.received_at,
                link=excluded.link,
                source=excluded.source,
                source_icon=excluded.source_icon,
                tier=excluded.tier,
                summary=excluded.summary,
                description=excluded.description,
                full_content=excluded.full_content,
                label_names=excluded.label_names
            """,
            (
                record["gmail_message_id"],
                record["thread_id"],
                record["subject"],
                record["sender"],
                record["received_at"],
                record["link"],
                record["source"],
                record["source_icon"],
                record["tier"],
                record["summary"],
                record["description"],
                record["full_content"],
                record["label_names"],
            ),
        )


def stored_message_ids() -> set[str]:
    with get_connection() as connection:
        rows = connection.execute("SELECT gmail_message_id FROM newsletters").fetchall()
    return {row["gmail_message_id"] for row in rows}


def auto_label_newsletters() -> dict[str, int]:
    """Scan inbox for emails from allowed domains and apply the Pulse label."""
    service = build_gmail_service()
    label_id = find_label_id(service, target_label())
    if not label_id:
        raise ValueError(f'Pulse label not found in Gmail.')

    domains = allowed_domains()
    base_query = " OR ".join(f"from:@{d}" for d in domains)
    query = f"({base_query}) newer_than:10d"
    response = service.users().messages().list(
        userId="me", q=query, maxResults=200
    ).execute()

    messages = response.get("messages", [])
    labeled = 0
    already = 0
    archived = 0
    for msg in messages:
        meta = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From"]
        ).execute()
        existing_labels = meta.get("labelIds", [])
        if label_id in existing_labels:
            already += 1
            # Archive if still in inbox
            if "INBOX" in existing_labels:
                service.users().messages().modify(
                    userId="me", id=msg["id"],
                    body={"removeLabelIds": ["INBOX"]}
                ).execute()
                archived += 1
            continue
        service.users().messages().modify(
            userId="me", id=msg["id"],
            body={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]}
        ).execute()
        labeled += 1

    return {"labeled": labeled, "already_labeled": already, "archived": archived}


def sync_newsletters(force: bool = False) -> dict[str, int]:
    service = build_gmail_service()
    existing_ids = stored_message_ids()
    label_id = find_label_id(service, target_label())
    if not label_id:
        raise ValueError(
            f'Gmail label "{target_label()}" was not found. Create it in Gmail and apply it to your newsletters first.'
        )

    response = (
        service.users()
        .messages()
        .list(userId="me", labelIds=[label_id], maxResults=max_results())
        .execute()
    )

    inserted = 0
    updated = 0
    skipped_existing = 0
    skipped_sender = 0
    messages = response.get("messages", [])

    for item in messages:
        message_id = item["id"]
        already_exists = message_id in existing_ids
        if already_exists and not force:
            skipped_existing += 1
            continue

        payload = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = payload.get("payload", {}).get("headers", [])
        sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "")
        subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "(No subject)")

        if not sender_allowed(sender):
            skipped_sender += 1
            continue

        plain_text, html_text = extract_parts(payload.get("payload", {}))
        content = clean_newsletter_content(
            plain_text=plain_text,
            html_text=html_text,
            snippet=payload.get("snippet", ""),
        )
        preview, cleaned_content = extract_preview(normalize_newsletter_lines(content), subject)
        if cleaned_content:
            content = cleaned_content

        summary, description = extract_summary_and_description(content, subject)
        if not summary:
            summary = preview if preview and preview != "No summary available." else build_summary(subject, content)
        if not description:
            description = build_description_from_content(content, summary, char_limit=500)
        source, source_icon = detect_source(sender, subject)
        tier = detect_tier(subject, content)
        signal = newsletter_signal(subject, content)

        save_newsletter(
            {
                "gmail_message_id": message_id,
                "thread_id": payload.get("threadId", ""),
                "subject": subject,
                "sender": sender,
                "received_at": parse_received_at(headers),
                "link": newsletter_link(message_id),
                "source": source,
                "source_icon": source_icon,
                "tier": tier,
                "summary": summary,
                "description": description,
                "full_content": content,
                "label_names": f"{target_label()}|signal:{signal}",
            }
        )

        if already_exists:
            updated += 1
        else:
            inserted += 1

    return {
        "label_id": label_id,
        "fetched": len(messages),
        "inserted": inserted,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_sender": skipped_sender,
    }


def purge_old_newsletters(days: int = 7) -> int:
    with get_connection() as connection:
        result = connection.execute(
            "DELETE FROM newsletters WHERE received_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        return result.rowcount


def load_newsletters(limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                gmail_message_id,
                subject,
                source,
                source_icon,
                received_at,
                link,
                tier,
                summary,
                description,
                full_content,
                label_names
            FROM newsletters
            WHERE label_names NOT LIKE '%signal:low%'
              AND received_at >= datetime('now', '-7 days')
            ORDER BY received_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    newsletters: list[dict[str, Any]] = []
    for row in rows:
        newsletters.append(
            {
                "title": row["subject"],
                "summary": row["summary"],
                "description": row["description"],
                "fullContent": row["full_content"],
                "link": row["link"],
                "pubDate": row["received_at"],
                "source": row["source"],
                "sourceIcon": row["source_icon"],
                "category": "newsletter",
                "isNewsletter": True,
                "emailId": row["gmail_message_id"],
                "nlTier": row["tier"],
                "signal": row["label_names"],
            }
        )
    return newsletters


def alert_recipient() -> str:
    return os.getenv("PULSE_DEADLINE_ALERT_TO", "lgavin689@gmail.com")


def send_email(subject: str, html_body: str, to: str | None = None) -> dict:
    """Send an HTML email from the authenticated account. Used by the
    program-deadline watcher; kept generic for future alert types."""
    from email.mime.text import MIMEText

    recipient = to or alert_recipient()
    message = MIMEText(html_body, "html")
    message["to"] = recipient
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service = build_gmail_service()
    return (
        service.users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
