import os
import re
import time
import json
import uuid
import hashlib
import datetime
import requests
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# =========================================================
# 💡 BỘ GIÁP STEALTH
# =========================================================
def apply_stealth(page):
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except ImportError:
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass
    except Exception:
        pass

# =========================================================
# CONFIG XÂY CON TV
# =========================================================
TARGET_SITE   = "https://sv2.xaycon7.live/lich-thi-dau/bong-da?by=state&value=live"
BASE_URL      = "https://sv2.xaycon7.live"
FILE_PATH     = "xaycon.json"
LIMIT_MATCHES = 10

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dauxaycon")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# =========================================================
# HELPER
# =========================================================
def make_id(seed: str = "") -> str:
    h = hashlib.md5((seed or str(uuid.uuid4())).encode()).hexdigest()
    return f"xaycon-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

def get_final_logo(team_name: str, site_logo: str) -> str:
    if site_logo and site_logo.startswith("http"): return site_logo
    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# JS: EXTRACT DATA (CHUẨN HTML XÂY CON)
# =========================================================
JS_EXTRACT = """
() => {
    const results = [];
    const seen = new Set();
    const clean = t => (t || '').replace(/\\s+/g, ' ').trim();

    // Lấy tất cả các thẻ a dẫn vào phòng xem bóng đá
    const anchors = Array.from(document.querySelectorAll('a[href*="/truc-tiep/bong-da/"]'));

    for (const a of anchors) {
        const href = a.href;
        if (seen.has(href)) continue;
        seen.add(href);

        // Lấy giải đấu (K League 2, Super League...)
        let league = '';
        const leagueEl = a.querySelector('p.text-sm');
        if (leagueEl) league = clean(leagueEl.innerText);

        let home = '', away = '', homeLogo = '', awayLogo = '';
        
        // Khối grid chứa thông tin 2 đội
        const gridBox = a.querySelector('div[class*="grid-cols-[1fr_auto_1fr]"]');
        
        if (gridBox && gridBox.children.length >= 3) {
            // Tên đội nằm trong thẻ span.truncate
            const homeSpan = gridBox.children[0].querySelector('span.truncate');
            const awaySpan = gridBox.children[2].querySelector('span.truncate');
            if (homeSpan) home = clean(homeSpan.innerText);
            if (awaySpan) away = clean(awaySpan.innerText);
            
            // Logo nằm trong thẻ img
            const imgNha = gridBox.children[0].querySelector('img');
            const imgKhach = gridBox.children[2].querySelector('img');
            if (imgNha) homeLogo = imgNha.src;
            if (imgKhach) awayLogo = imgKhach.src;
        }

        // Lấy thời gian (Gộp 17:30 và 10/07 lại)
        let timeStr = '';
        const timeSpans = a.querySelectorAll('span.bg-yellow-300, span.text-\\[18px\\]');
        if (timeSpans.length >= 2) {
            timeStr = clean(timeSpans[0].innerText) + ' ' + clean(timeSpans[1].innerText);
        }

        // Check xem có đang Live không
        const isLive = clean(a.innerText).toLowerCase().includes('trực tiếp');

        // Tìm tên BLV (BLV Ciu, BLV Rồng Đất...)
        let blvName = "BLV Mặc định";
        const allSpans = Array.from(a.querySelectorAll('div, span, p'));
        const blvEl = allSpans.find(el => clean(el.innerText).toUpperCase().startsWith('BLV '));
        if (blvEl) {
            blvName = clean(blvEl.innerText);
        }

        if (home && away) {
            results.push({ href, home, away, timeStr, homeLogo, awayLogo, tournament: league, isLive, blvName });
        }
    }
    return results;
}
"""

# =========================================================
# CAPTURE STREAM (BẮT LINK M3U8 CỦA XÂY CON)
# =========================================================
def capture_stream(context, match_url: str) -> list:
    page = context.new_page()
    apply_stealth(page)
    
    streams = set()
    page.on("request", lambda req: streams.add(req.url) if ".m3u8" in req.url.lower() and "/ad/" not in req.url.lower() else None)
    
    try:
        page.goto(match_url, wait_until="load", timeout=40000)
        page.wait_for_timeout(6000)
    except Exception:
        pass
    finally:
        page.close()
    
    scored = []
    for s in streams:
        score = 0
        if "100ycdn.com" in s.lower(): score += 5000
        if "edgemaxcdn" in s.lower(): score += 4500
        if "xclive" in s.lower(): score += 6000
        scored.append((score, s))
    
    scored.sort(reverse=True, key=lambda x: x[0])
    return [s for sc, s in scored]

# =========================================================
# BUILD CHANNEL (MAP DỮ LIỆU ĐÃ GỘP)
# =========================================================
def build_channel(m, stream_data):
    home = (m.get('home') or "Unknown").title()
    away = (m.get('away') or "Unknown").title()
    thoi_gian = m.get('timeStr') or "Không rõ"
    
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}" + (f" | {m.get('tournament')}" if m.get('tournament') else "") + f" | {thoi_gian}"

    cid = make_id(m['href'])
    is_live = len(stream_data) > 0
    
    label_text = "● Live" if is_live else ("🔴 Chờ stream" if m.get('isLive') else "⏳ Chưa live")
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get('isLive') else "#d54f1a")

    stream_links = []
    for i, s in enumerate(stream_data):
        stream_links.append({
            "id": make_link_id(),
            "name": s["name"],
            "type": "hls",
            "default": i == 0,
            "url": s["url"]
        })

    return {
        "id": cid, "name": display_name, 
        "tournament": m.get("tournament", ""),
        "logo_nha": get_final_logo(home, m.get('homeLogo')), 
        "logo_khach": get_final_logo(away, m.get('awayLogo')),
        "type": "single", "display": "thumbnail-only", "enable_detail": False,
        "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": get_final_logo(home, m.get('homeLogo')), "width": 1600, "height": 1200},
        "labels": [{"text": label_text, "position": "top-left", "color": "#00ffffff", "text_color": label_color}],
        "sources": [{
            "id": cid, "name": "Xây Con TV",
            "contents": [{
                "id": cid, "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": stream_links}]
            }]
        }],
    }

# =========================================================
# CHƯƠNG TRÌNH CHÍNH (THUẬT TOÁN GỘP TRẬN)
# =========================================================
def scrape_and_push():
    now_vn = datetime.datetime.now(VN_TZ)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT XÂY CON (Giờ VN): {now_str}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        apply_stealth(page)
        
        try:
            print("📺 Đang mở trang Xây Con...")
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
        except Exception as e:
            print(f"⚠️ Web load chậm, vẫn tiếp tục: {e}")
            
        raw_matches = page.evaluate(JS_EXTRACT)
        
        # 💡 THUẬT TOÁN GỘP TRẬN (Nhóm các BLV chung 1 trận)
        grouped_matches = {}
        for m in raw_matches:
            h_lower = (m.get('home') or "").lower()
            a_lower = (m.get('away') or "").lower()
            if not h_lower or not a_lower or "unknown" in h_lower: continue
            
            key = f"{h_lower} vs {a_lower}"
            blv_name = m.get('blvName', 'BLV Mặc định')
            
            if key not in grouped_matches:
                m['hrefs_and_blvs'] = [(m['href'], blv_name)]
                grouped_matches[key] = m
            else:
                grouped_matches[key]['hrefs_and_blvs'].append((m['href'], blv_name))

        valid_matches = list(grouped_matches.values())[:LIMIT_MATCHES]
        
        channels = []
        for idx, m in enumerate(valid_matches, 1):
            print(f"\n[{idx}/{len(valid_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
            
            all_match_streams = []
            for href, blv_name in m['hrefs_and_blvs']:
                print(f"      > Cào luồng: {blv_name}...")
                streams = capture_stream(context, href)
                if streams:
                    all_match_streams.append({"name": blv_name, "url": streams[0]})

            channels.append(build_channel(m, all_match_streams))

    # Đẩy lên GitHub
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    content = json.dumps({
        "id": "xaycon", "name": "Xây Con TV", "last_updated": now_str, 
        "groups": [{"id": "live", "name": "🔴 Live bóng đá Xây Con", "channels": channels}]
    }, indent=2, ensure_ascii=False)
    
    msg = f"⚽ Update Xây Con (VN Time): {now_str}"
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
    except:
        repo.create_file(FILE_PATH, msg, content)
    print("\n✅ HOÀN TẤT CẬP NHẬT XÂY CON!")

if __name__ == "__main__":
    scrape_and_push()
