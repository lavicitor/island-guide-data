import argparse
import html as _html
import json
import os
import sys
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Shared CSS (used by both island pages and index)
# ---------------------------------------------------------------------------

COMMON_CSS = """\
    :root {
      --bg:      #FFFFFF;
      --surface: #F8FAFC;
      --text:    #1E293B;
      --muted:   #64748B;
      --border:  #E2E8F0;
      --accent:  #0891B2;
      --success: #10B981;
      --warning: #F59E0B;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg:      #0F172A;
        --surface: #1E293B;
        --text:    #F1F5F9;
        --muted:   #94A3B8;
        --border:  #334155;
        --accent:  #22D3EE;
        --success: #34D399;
        --warning: #FCD34D;
      }
    }
    body { font-family: 'Inter', sans-serif; }
    [hidden] { display: none !important; }"""

ISLAND_EXTRA_CSS = """\
    @media (min-width: 768px) { #map { height: 420px; } }
    .poi-card:hover { border-color: var(--accent) !important; }"""


# ---------------------------------------------------------------------------
# Data helpers (unchanged)
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def merge_ferry_routes(data_routes, community_routes):
    """Merge community ferry data into data routes, matched by origin+operator."""
    community_index = {
        (r["origin"], r["operator"]): r for r in community_routes
    }
    merged = []
    for route in data_routes:
        key = (route["origin"], route["operator"])
        community = community_index.get(key, {})
        merged_route = {**route, **{k: v for k, v in community.items() if v is not None}}
        merged.append(merged_route)
    return merged


def merge_pois_by_name(data_pois, community_pois, name_field="osm_name"):
    """Enrich data POIs with community data matched by name."""
    community_index = {p[name_field]: p for p in community_pois if name_field in p}
    result = []
    for poi in data_pois:
        poi_name = poi.get("name")
        community = community_index.get(poi_name, {})
        result.append({**poi, **community})
    # Add community-only POIs (e.g. Toreta Tower, which has no OSM entry)
    data_names = {p.get("name") for p in data_pois}
    for cpoi in community_pois:
        if cpoi.get(name_field) not in data_names:
            result.append(cpoi)
    return result


def merge(data, community):
    merged = json.loads(json.dumps(data))  # deep copy

    # identity: community fills in nulls and extends lists
    for key, val in community.get("identity", {}).items():
        if val is not None and val != [] and val != {}:
            merged["identity"][key] = val

    # getting_there: merge ferry routes, add community fields
    c_getting = community.get("getting_there", {})
    if "ferry_routes" in c_getting:
        merged["getting_there"]["ferry_routes"] = merge_ferry_routes(
            data["getting_there"]["ferry_routes"],
            c_getting["ferry_routes"],
        )
    for key in ("ferry_notes", "practical_tips"):
        if key in c_getting:
            merged["getting_there"][key] = c_getting[key]

    # practical: community overrides take precedence
    for key, val in community.get("practical", {}).items():
        if val is not None:
            merged["practical"][key] = val

    # nearby_islands: community provides enriched objects; use them if present
    if "nearby_islands" in community:
        merged["practical"]["nearby_islands"] = community["nearby_islands"]

    # POIs: enrich with community tips/descriptions
    pois = merged.setdefault("points_of_interest", {})
    if "beaches" in community:
        pois["beaches"] = merge_pois_by_name(
            pois.get("beaches", []), community["beaches"]
        )
    if "restaurants" in community:
        pois["restaurants"] = merge_pois_by_name(
            pois.get("restaurants", []), community["restaurants"]
        )
    if "landmarks" in community:
        pois["landmarks"] = merge_pois_by_name(
            pois.get("landmarks", []), community["landmarks"]
        )

    # community-only sections
    for key in ("hidden_gems", "local_warnings"):
        if key in community:
            merged[key] = community[key]

    return merged


def summarise(merged):
    identity = merged.get("identity", {})
    pois = merged.get("points_of_interest", {})
    weather = merged.get("weather", {})
    practical = merged.get("practical", {})

    print(f"Island: {identity.get('name')} ({identity.get('region')})")
    print(f"  Area: {identity.get('area_km2')} km²  |  "
          f"Population: {identity.get('population', 'unknown')}  |  "
          f"Car-free: {identity.get('car_free')}")
    print(f"  Tagline: {identity.get('tagline', '—')}")
    print(f"  Best for: {', '.join(identity.get('best_for', []) or ['—'])}")
    print()

    print("POI counts (after merge):")
    for category in ("beaches", "restaurants", "atms", "accommodation", "landmarks"):
        items = pois.get(category, [])
        named = sum(1 for i in items if i.get("name") or i.get("osm_name") or i.get("title"))
        print(f"  {category:<15} {len(items):>3} total  ({named} named)")

    gems = merged.get("hidden_gems", [])
    if gems:
        print(f"  {'hidden_gems':<15} {len(gems):>3} total")
    warnings = merged.get("local_warnings", [])
    if warnings:
        print(f"  {'local_warnings':<15} {len(warnings):>3} entries")
    print()

    print("Weather:")
    best = weather.get("best_months", [])
    peak = weather.get("peak_season_months", [])
    complete = len(weather.get("monthly", []))
    print(f"  Monthly data: {complete}/12 months")
    print(f"  Best months:  {', '.join(best) if best else '—'}")
    print(f"  Peak season:  {', '.join(peak) if peak else '—'}")
    print()

    nearby = practical.get("nearby_islands", [])
    print(f"Nearby islands ({len(nearby)}):")
    for island in nearby:
        if isinstance(island, dict):
            name = island.get("name", "?")
            mins = island.get("duration_minutes")
            desc = island.get("worth_visiting_for", "")
            suffix = f"  ~{mins} min" if mins else ""
            print(f"  {name}{suffix}: {desc}")
        else:
            print(f"  {island}")
    print()

    ferry_routes = merged.get("getting_there", {}).get("ferry_routes", [])
    print(f"Ferry routes: {len(ferry_routes)}")
    for r in ferry_routes:
        dur = f"{r.get('duration_minutes')} min" if r.get("duration_minutes") else "? min"
        freq_peak = r.get("frequency_peak", "?")
        print(f"  {r.get('origin')} → Silba via {r.get('operator')}  ({dur}, {freq_peak}x/week peak)")


# ---------------------------------------------------------------------------
# Island page generation
# ---------------------------------------------------------------------------

def generate_island_page(merged, slug):
    os.makedirs("islands", exist_ok=True)

    identity = merged.get("identity", {})
    weather = merged.get("weather", {})
    getting_there = merged.get("getting_there", {})
    pois = merged.get("points_of_interest", {})
    practical = merged.get("practical", {})
    meta_data = merged.get("meta", {})
    hidden_gems = merged.get("hidden_gems", [])
    local_warnings = merged.get("local_warnings", [])

    name = identity.get("name", slug.title())
    region = identity.get("region", "")
    area = identity.get("area_km2")
    population = identity.get("population")
    car_free = identity.get("car_free", False)
    tagline = identity.get("tagline", "")
    coords = identity.get("coordinates", {})
    lat = coords.get("lat", 44.37)
    lon = coords.get("lon", 14.70)
    generated_at = meta_data.get("generated_at", "")

    monthly = weather.get("monthly", [])
    best_months_set = set(weather.get("best_months", []))
    peak_months_set = set(weather.get("peak_season_months", []))

    MONTH_ABBREVS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def h(s):
        if s is None:
            return ""
        return _html.escape(str(s))

    def fmt_duration(mins):
        if mins is None:
            return "—"
        hh, mm = divmod(int(mins), 60)
        if hh and mm:
            return f"{hh}h {mm}m"
        return f"{hh}h" if hh else f"{mm}m"

    def is_cash_only(poi):
        tags = poi.get("osm_tags") or {}
        payment_str = (poi.get("payment") or "").lower()
        return (
            (tags.get("payment:cash") == "yes" and tags.get("payment:cards") == "no")
            or "cash only" in payment_str
        )

    def small_chip(text, style="default"):
        styles = {
            "default": "background:var(--surface);border:1px solid var(--border);color:var(--muted)",
            "success": "background:#D1FAE5;color:#065F46",
            "danger":  "background:#FEE2E2;color:#991B1B",
            "warning": "background:#FEF3C7;color:#92400E",
            "accent":  "background:color-mix(in srgb,var(--accent) 15%,var(--surface));color:var(--accent)",
        }
        return (
            '<span class="text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap"'
            f' style="{styles.get(style, styles["default"])}">'
            + h(text) + "</span>"
        )

    def transport_chip(val, label):
        if val is True:
            return small_chip(f"✓ {label}", "success")
        if val is False:
            return small_chip(f"✗ {label}", "danger")
        return small_chip(f"? {label}", "default")

    # ------------------------------------------------------------------
    # 1. Header
    # ------------------------------------------------------------------
    header = (
        '<header class="sticky top-0 z-50 h-12 flex items-center px-4 md:px-6 gap-4"'
        ' style="background:var(--bg);border-bottom:1px solid var(--border)">'
        '<a href="../index.html" class="text-sm shrink-0 hover:underline"'
        ' style="color:var(--accent)">← All Islands</a>'
        f'<h2 class="flex-1 text-center md:text-left font-semibold truncate"'
        f' style="color:var(--text)">{h(name)}</h2>'
        "</header>"
    )

    # ------------------------------------------------------------------
    # 2. Hero
    # ------------------------------------------------------------------
    meta_parts = []
    if region:
        meta_parts.append(h(region))
    if area is not None:
        meta_parts.append(f"{area} km²")
    if population is not None:
        meta_parts.append(f"Pop. {population:,}")

    car_free_badge = (
        '<span class="inline-block text-white text-xs px-3 py-1 rounded-full font-medium mt-3"'
        ' style="background:var(--accent)">Car-free island</span>'
        if car_free else ""
    )
    tagline_html = (
        f'<p class="italic mt-2 text-sm" style="color:var(--muted)">{h(tagline)}</p>'
        if tagline else ""
    )

    hero = (
        '<section class="px-4 md:px-6 pt-6 pb-4">'
        f'<h1 class="text-[28px] font-bold" style="color:var(--text)">{h(name)}</h1>'
        + (f'<p class="text-sm mt-1" style="color:var(--muted)">{" · ".join(meta_parts)}</p>'
           if meta_parts else "")
        + car_free_badge
        + tagline_html
        + "</section>"
    )

    # ------------------------------------------------------------------
    # 3. Season strip
    # ------------------------------------------------------------------
    month_chips = []
    for i, m_data in enumerate(monthly):
        month_name = m_data.get("month_name", "")
        abbrev = MONTH_ABBREVS[i] if i < 12 else month_name[:3]
        if month_name in peak_months_set:
            chip_style = "background:var(--accent);color:#fff"
        elif month_name in best_months_set:
            chip_style = "background:color-mix(in srgb,var(--accent) 15%,var(--surface));color:var(--accent)"
        else:
            chip_style = "background:var(--surface);color:var(--muted)"
        month_chips.append(
            f'<span class="inline-block text-xs font-medium px-3 py-1.5 rounded-full whitespace-nowrap"'
            f' style="{chip_style}">{abbrev}</span>'
        )

    season_legend = (
        '<div class="flex gap-4 mt-2 px-4 md:px-6 text-xs" style="color:var(--muted)">'
        '<span class="flex items-center gap-1">'
        '<span class="inline-block w-3 h-3 rounded-full" style="background:var(--accent)"></span> Peak</span>'
        '<span class="flex items-center gap-1">'
        '<span class="inline-block w-3 h-3 rounded-full"'
        ' style="background:color-mix(in srgb,var(--accent) 15%,var(--surface));'
        'border:1px solid var(--accent)"></span> Best</span>'
        "</div>"
    )

    season = (
        '<section class="py-4">'
        '<div class="overflow-x-auto">'
        '<div class="flex gap-2 px-4 md:px-6 pb-2" style="min-width:max-content">'
        + "".join(month_chips)
        + "</div></div>"
        + season_legend
        + "</section>"
    )

    # ------------------------------------------------------------------
    # 4. Getting there
    # ------------------------------------------------------------------
    ferry_routes = getting_there.get("ferry_routes", [])
    ferry_notes = getting_there.get("ferry_notes", "")
    practical_tips = getting_there.get("practical_tips", [])

    route_cards = []
    for route in ferry_routes:
        dur = fmt_duration(route.get("duration_minutes"))
        fp = route.get("frequency_peak")
        fl = route.get("frequency_low")
        if fp is not None:
            freq_str = f"{fp}×/week peak"
            if fl is not None:
                freq_str += f" · {fl}×/week off-season"
        else:
            freq_str = "—"

        bikes_chip = transport_chip(route.get("bikes_allowed"), "Bikes")
        cars_chip = transport_chip(route.get("cars_allowed"), "Cars")

        booking_url = route.get("booking_url", "")
        book_btn = (
            f'<a href="{h(booking_url)}" target="_blank" rel="noopener"'
            ' class="inline-block mt-3 text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"'
            ' style="background:var(--accent);color:#fff">'
            "Book tickets →</a>"
        ) if booking_url else ""

        community_note = route.get("community_note", "")
        note_html = (
            f'<p class="text-xs mt-1 italic" style="color:var(--muted)">{h(community_note)}</p>'
            if community_note else ""
        )

        route_cards.append(
            '<div class="rounded-lg p-4" style="border:1px solid var(--border)">'
            '<div class="flex items-start gap-2">'
            f'<i data-lucide="anchor" class="w-4 h-4 shrink-0 mt-0.5" style="color:var(--accent)"></i>'
            f'<span class="font-semibold" style="color:var(--text)">{h(route.get("origin", ""))}</span>'
            "</div>"
            f'<p class="text-sm mt-1" style="color:var(--muted)">{h(route.get("operator", ""))}</p>'
            + note_html
            + '<div class="flex flex-wrap gap-x-6 gap-y-1 mt-2 text-sm">'
            f'<span><span style="color:var(--muted)">Duration:</span> {dur}</span>'
            f'<span><span style="color:var(--muted)">Frequency:</span> {freq_str}</span>'
            "</div>"
            f'<div class="flex gap-2 mt-3">{bikes_chip}{cars_chip}</div>'
            + book_btn
            + "</div>"
        )

    ferry_notes_html = (
        '<div class="rounded-lg p-4 flex gap-3 mt-3"'
        ' style="background:color-mix(in srgb,var(--accent) 10%,var(--bg));'
        'border:1px solid color-mix(in srgb,var(--accent) 30%,transparent)">'
        f'<i data-lucide="info" class="w-4 h-4 shrink-0 mt-0.5" style="color:var(--accent)"></i>'
        f'<p class="text-sm" style="color:var(--text)">{h(ferry_notes)}</p>'
        "</div>"
    ) if ferry_notes else ""

    tips_html = ""
    if practical_tips:
        items = "".join(
            f'<li class="flex gap-2 text-sm"><span style="color:var(--muted)" class="shrink-0">•</span>'
            f'<span style="color:var(--text)">{h(t)}</span></li>'
            for t in practical_tips
        )
        tips_html = f'<ul class="space-y-2 mt-3">{items}</ul>'

    getting_there_html = (
        '<section class="px-4 md:px-6 py-6">'
        '<p class="text-[13px] font-semibold uppercase tracking-wider mb-4"'
        ' style="color:var(--muted)">Getting There</p>'
        '<div class="grid gap-3 md:grid-cols-2">'
        + "".join(route_cards)
        + "</div>"
        + ferry_notes_html
        + tips_html
        + "</section>"
    )

    # ------------------------------------------------------------------
    # 5. Map
    # ------------------------------------------------------------------
    CATEGORY_LABELS = {
        "beaches":       "Beach",
        "restaurants":   "Restaurant",
        "atms":          "ATM",
        "accommodation": "Accommodation",
        "landmarks":     "Landmark",
    }

    marker_data = []
    cat_config = {
        "beaches":       "#0891B2",
        "restaurants":   "#F97316",
        "atms":          "#10B981",
        "accommodation": "#8B5CF6",
        "landmarks":     "#EF4444",
    }
    for category, color in cat_config.items():
        for poi in pois.get(category, []):
            c = poi.get("coordinates") or {}
            plat, plon = c.get("lat"), c.get("lon")
            if plat is None or plon is None:
                continue
            poi_name = (poi.get("name") or poi.get("osm_name")
                        or CATEGORY_LABELS.get(category, category.title()))
            tags = poi.get("osm_tags") or {}
            marker_color = "#1E293B" if tags.get("amenity") == "ferry_terminal" else color
            marker_data.append({
                "lat": plat, "lon": plon,
                "name": poi_name,
                "category": category,
                "color": marker_color,
            })

    map_section = (
        '<section class="px-4 md:px-6 py-6">'
        '<div id="map" class="w-full rounded-lg overflow-hidden"'
        ' style="height:300px;border:1px solid var(--border)"></div>'
        '</section>'
    )

    # Map JS — uses .replace() to avoid brace-doubling issues with f-strings.
    # Single-brace {s}/{z}/{x}/{y}/{r} in tile URLs are Leaflet placeholders,
    # left as-is. Double-brace {{...}} marks JS object literals and gets
    # un-escaped at the end.
    map_js = (
        "(function(){"
        "var markers=__MARKERS__;"
        "var prefersDark=window.matchMedia('(prefers-color-scheme: dark)').matches;"
        "var tileUrl=prefersDark"
        "?'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'"
        ":'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png';"
        "var map=L.map('map').setView([__LAT__,__LON__],13);"
        "L.tileLayer(tileUrl,{{attribution:'\\u00a9 OpenStreetMap contributors \\u00a9 CARTO',"
        "subdomains:'abcd',maxZoom:19}}).addTo(map);"
        "markers.forEach(function(m){"
        "var mk=L.circleMarker([m.lat,m.lon],"
        "{{radius:7,fillColor:m.color,color:'#fff',weight:2,opacity:1,fillOpacity:0.9}})"
        ".addTo(map);"
        "mk.bindPopup('<strong>'+m.name+'</strong><br>"
        "<span style=\"font-size:12px\">'+m.category+'</span>');"
        "});})();"
    )
    map_js = (
        map_js
        .replace("__MARKERS__", json.dumps(marker_data))
        .replace("__LAT__", str(lat))
        .replace("__LON__", str(lon))
        .replace("{{", "{")
        .replace("}}", "}")
    )

    # ------------------------------------------------------------------
    # 6. Weather chart
    # ------------------------------------------------------------------
    temp_labels = json.dumps([m.get("month_name", "")[:3] for m in monthly])
    temp_air    = json.dumps([m.get("avg_temp_c") for m in monthly])
    temp_sea    = json.dumps([m.get("sea_temp_c") for m in monthly])

    best_chips = "".join(small_chip(m, "accent") for m in weather.get("best_months", []))
    peak_chips = "".join(small_chip(m, "default") for m in weather.get("peak_season_months", []))

    weather_html = (
        '<section class="px-4 md:px-6 py-6">'
        '<p class="text-[13px] font-semibold uppercase tracking-wider mb-4"'
        ' style="color:var(--muted)">Weather</p>'
        '<div style="position:relative;height:220px"><canvas id="weatherChart"></canvas></div>'
        '<div class="flex flex-wrap gap-4 mt-4 text-sm">'
        + (f'<div class="flex items-center gap-2"><span style="color:var(--muted)">Best months:</span>'
           f'<div class="flex gap-1 flex-wrap">{best_chips}</div></div>' if best_chips else "")
        + (f'<div class="flex items-center gap-2"><span style="color:var(--muted)">Peak season:</span>'
           f'<div class="flex gap-1 flex-wrap">{peak_chips}</div></div>' if peak_chips else "")
        + "</div></section>"
    )

    # Chart JS — reads CSS variables at runtime so tick/label colours adapt to dark mode.
    chart_js = (
        "(function(){"
        "var ctx=document.getElementById('weatherChart');"
        "if(!ctx)return;"
        "var cs=getComputedStyle(document.documentElement);"
        "var textColor=cs.getPropertyValue('--text').trim();"
        "var mutedColor=cs.getPropertyValue('--muted').trim();"
        "var borderColor=cs.getPropertyValue('--border').trim();"
        "new Chart(ctx,{"
        "data:{"
        "labels:__LABELS__,"
        "datasets:["
        "{"
        "type:'bar',"
        "label:'Air temp (\\u00b0C)',"
        "data:__AIR__,"
        "backgroundColor:'#0891B2cc',"
        "borderColor:'#0891B2',"
        "borderWidth:1,"
        "yAxisID:'y'"
        "},{"
        "type:'line',"
        "label:'Sea temp (\\u00b0C)',"
        "data:__SEA__,"
        "borderColor:'#F97316',"
        "backgroundColor:'#F9731620',"
        "pointBackgroundColor:'#F97316',"
        "tension:0.4,"
        "yAxisID:'y'"
        "}"
        "]"
        "},"
        "options:{"
        "responsive:true,"
        "maintainAspectRatio:false,"
        "plugins:{legend:{position:'bottom',labels:{boxWidth:12,font:{size:11},color:textColor}}},"
        "scales:{"
        "y:{title:{display:true,text:'\\u00b0C',color:mutedColor},"
        "grid:{color:borderColor},ticks:{color:mutedColor}},"
        "x:{grid:{display:false},ticks:{color:mutedColor}}"
        "}"
        "}"
        "});"
        "})();"
    )
    chart_js = (
        chart_js
        .replace("__LABELS__", temp_labels)
        .replace("__AIR__", temp_air)
        .replace("__SEA__", temp_sea)
    )

    # ------------------------------------------------------------------
    # 7. POI sections
    # ------------------------------------------------------------------
    def render_poi_card(poi, icon_lucide, icon_color):
        poi_name = poi.get("name") or poi.get("osm_name") or poi.get("title")
        if not poi_name and not poi.get("one_liner"):
            return ""

        display_name = poi_name or "Unnamed"
        one_liner = poi.get("one_liner") or ""
        if not one_liner:
            tags = poi.get("osm_tags") or {}
            hints = []
            if tags.get("cuisine"):
                hints.append(tags["cuisine"].title())
            if tags.get("surface"):
                hints.append(f"Surface: {tags['surface']}")
            if tags.get("amenity") not in (None, "restaurant", "cafe", "atm"):
                hints.append(tags["amenity"].replace("_", " ").title())
            one_liner = ", ".join(hints)

        standout = poi.get("standout_tip") or ""
        tips_list = poi.get("tips") or []
        cash = is_cash_only(poi)
        cash_chip_html = small_chip("Cash only", "warning") if cash else ""

        standout_html = (
            f'<p class="text-sm italic mt-1" style="color:var(--muted)">💬 {h(standout)}</p>'
            if standout else ""
        )

        tips_items = "".join(
            f'<li class="flex gap-2 text-sm">'
            f'<span class="shrink-0" style="color:var(--muted)">•</span>{h(t)}</li>'
            for t in tips_list
        )
        tips_html = (
            f'<p class="text-sm font-semibold mt-2 mb-1" style="color:var(--muted)">💬 Local tips</p>'
            f'<ul class="space-y-1">{tips_items}</ul>'
        ) if tips_items else ""

        meta_chips = []
        for attr in ("surface", "price_range", "seasonal"):
            if poi.get(attr):
                meta_chips.append(small_chip(poi[attr]))
        meta_chips_html = (
            '<div class="flex flex-wrap gap-2 mt-3">' + "".join(meta_chips) + "</div>"
        ) if meta_chips else ""

        detail_inner = tips_html + meta_chips_html
        has_detail = bool(detail_inner.strip())
        detail_block = (
            f'<div class="card-detail" hidden>{detail_inner}</div>'
            if has_detail else ""
        )
        chevron_html = (
            '<span class="chevron text-lg shrink-0 select-none"'
            ' style="color:var(--muted)">›</span>'
            if has_detail else ""
        )
        onclick_attr = ' onclick="toggleCard(this)"' if has_detail else ""
        extra_cls = " poi-card cursor-pointer" if has_detail else ""

        return (
            f'<div class="rounded-lg p-4 transition-colors{extra_cls}"{onclick_attr}'
            f' style="border:1px solid var(--border)">'
            '<div class="flex items-start gap-3">'
            f'<div class="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5"'
            f' style="background:{icon_color}20">'
            f'<i data-lucide="{icon_lucide}" class="w-4 h-4" style="color:{icon_color}"></i>'
            "</div>"
            '<div class="flex-1 min-w-0">'
            '<div class="flex items-center gap-2 flex-wrap">'
            f'<span class="font-semibold" style="color:var(--text)">{h(display_name)}</span>'
            + cash_chip_html
            + "</div>"
            + (f'<p class="text-sm mt-0.5" style="color:var(--muted)">{h(one_liner)}</p>'
               if one_liner else "")
            + standout_html
            + detail_block
            + "</div>"
            + chevron_html
            + "</div></div>"
        )

    beaches_cards = "".join(
        render_poi_card(b, "waves", "#0891B2") for b in pois.get("beaches", [])
    )
    restaurants_cards = "".join(
        render_poi_card(r, "utensils", "#F97316") for r in pois.get("restaurants", [])
    )
    landmarks_cards = "".join(
        render_poi_card(lm, "landmark", "#EF4444") for lm in pois.get("landmarks", [])
    )

    def info_card(icon, icon_color, title, body):
        return (
            '<div class="rounded-lg p-4 flex items-start gap-3"'
            ' style="border:1px solid var(--border)">'
            f'<div class="w-8 h-8 rounded-full flex items-center justify-center shrink-0"'
            f' style="background:{icon_color}20">'
            f'<i data-lucide="{icon}" class="w-4 h-4" style="color:{icon_color}"></i>'
            "</div>"
            f'<div><p class="font-semibold" style="color:var(--text)">{h(title)}</p>'
            f'<p class="text-sm mt-0.5" style="color:var(--muted)">{h(body)}</p></div>'
            "</div>"
        )

    practical_cards = []
    atm_list = pois.get("atms", [])
    if atm_list:
        atm_name = atm_list[0].get("name", "ATM")
        practical_cards.append(info_card("banknote", "#10B981", atm_name,
                                         practical.get("cash_notes", "")))

    if practical.get("medical_facility"):
        practical_cards.append(info_card("stethoscope", "#10B981", "Medical",
                                         practical.get("medical_notes", "Seasonal medical facility.")))
    elif practical.get("medical_facility") is False:
        practical_cards.append(info_card("stethoscope", "#EF4444", "No medical facility",
                                         "Nearest hospital in Zadar."))

    accom_count = len(pois.get("accommodation", []))
    stay_nights = practical.get("recommended_stay_nights")
    day_trip_note = practical.get("day_trip_note") or ""
    if stay_nights:
        body = f"Recommended stay: {stay_nights} nights."
        if day_trip_note:
            body += f" {day_trip_note}"
        practical_cards.append(
            info_card("moon", "#8B5CF6", f"Accommodation ({accom_count} options)", body)
        )

    internet = practical.get("internet", "")
    if internet:
        practical_cards.append(info_card("wifi", "#0891B2", "Internet", internet))

    shopping = practical.get("shopping", "")
    if shopping:
        practical_cards.append(info_card("shopping-bag", "#64748B", "Shopping", shopping))

    def section_label(text):
        return (
            f'<p class="text-[13px] font-semibold uppercase tracking-wider mb-3"'
            f' style="color:var(--muted)">{text}</p>'
        )

    poi_html = (
        '<section class="px-4 md:px-6 py-6">'
        '<nav class="flex gap-3 flex-wrap text-sm mb-6">'
        f'<a href="#beaches" class="hover:underline" style="color:var(--accent)">Beaches</a>'
        f'<span style="color:var(--border)">·</span>'
        f'<a href="#food" class="hover:underline" style="color:var(--accent)">Food &amp; Drink</a>'
        f'<span style="color:var(--border)">·</span>'
        f'<a href="#practical" class="hover:underline" style="color:var(--accent)">Practical</a>'
        f'<span style="color:var(--border)">·</span>'
        f'<a href="#landmarks" class="hover:underline" style="color:var(--accent)">Landmarks</a>'
        "</nav>"

        f'<div id="beaches" class="mb-8">{section_label("Beaches")}'
        f'<div class="space-y-3">{beaches_cards}</div></div>'

        f'<div id="food" class="mb-8">{section_label("Food &amp; Drink")}'
        f'<div class="space-y-3">{restaurants_cards}</div></div>'

        f'<div id="practical" class="mb-8">{section_label("Practical")}'
        f'<div class="space-y-3">{"".join(practical_cards)}</div></div>'

        f'<div id="landmarks" class="mb-8">{section_label("Landmarks")}'
        f'<div class="space-y-3">{landmarks_cards}</div></div>'

        "</section>"
    )

    # ------------------------------------------------------------------
    # 8. Nearby islands
    # ------------------------------------------------------------------
    nearby = practical.get("nearby_islands", [])
    nearby_cards = []
    for island in nearby:
        if isinstance(island, dict):
            iname = island.get("name", "")
            dur = island.get("duration_minutes")
            desc = island.get("description", "")
            worth = island.get("worth_visiting_for", "")
            dur_html = (
                f'<span class="text-xs" style="color:var(--muted)">~{dur} min</span>'
                if dur else ""
            )
            nearby_cards.append(
                '<div class="rounded-lg p-4 min-w-[200px] md:min-w-0"'
                ' style="border:1px solid var(--border)">'
                '<div class="flex items-center gap-2">'
                f'<span class="font-semibold" style="color:var(--text)">{h(iname)}</span>'
                + dur_html + "</div>"
                + (f'<p class="text-sm mt-1" style="color:var(--muted)">{h(desc)}</p>'
                   if desc else "")
                + (f'<p class="text-xs mt-2 italic" style="color:var(--accent)">{h(worth)}</p>'
                   if worth else "")
                + "</div>"
            )
        else:
            nearby_cards.append(
                '<div class="rounded-lg p-4 min-w-[160px] md:min-w-0"'
                ' style="border:1px solid var(--border)">'
                f'<span class="font-semibold" style="color:var(--text)">{h(str(island))}</span>'
                "</div>"
            )

    nearby_html = (
        '<section class="px-4 md:px-6 py-6">'
        + section_label("Nearby Islands")
        + '<div class="overflow-x-auto">'
        '<div class="flex gap-3 pb-1 md:flex-wrap">'
        + "".join(nearby_cards)
        + "</div></div></section>"
    ) if nearby else ""

    # ------------------------------------------------------------------
    # 9. Local knowledge
    # ------------------------------------------------------------------
    knowledge_html = ""
    if hidden_gems or local_warnings:
        warnings_html = "".join(
            '<div class="rounded-r-lg p-4 flex gap-3"'
            ' style="border-left:4px solid var(--warning);'
            'background:color-mix(in srgb,var(--warning) 10%,var(--bg))">'
            '<span class="shrink-0" style="color:var(--warning)">⚠</span>'
            f'<p class="text-sm" style="color:var(--text)">{h(w)}</p>'
            "</div>"
            for w in local_warnings
        )
        gems_html = "".join(
            '<div class="rounded-lg p-4 flex gap-3"'
            ' style="background:var(--surface);border:1px solid var(--border)">'
            '<span class="shrink-0" style="color:var(--accent)">✦</span>'
            "<div>"
            + (f'<p class="font-semibold" style="color:var(--text)">{h(g.get("title",""))}</p>'
               if g.get("title") else "")
            + (f'<p class="text-sm mt-1" style="color:var(--muted)">{h(g.get("description",""))}</p>'
               if g.get("description") else "")
            + "</div></div>"
            for g in hidden_gems
        )
        knowledge_html = (
            '<section class="px-4 md:px-6 py-6">'
            '<p class="text-[13px] font-semibold uppercase tracking-wider"'
            ' style="color:var(--muted)">💬 Local Knowledge</p>'
            '<p class="text-xs mt-0.5 mb-4" style="color:var(--muted)">Curated tips — not scraped</p>'
            + (f'<div class="space-y-3 mb-4">{warnings_html}</div>' if warnings_html else "")
            + (f'<div class="space-y-3">{gems_html}</div>' if gems_html else "")
            + "</section>"
        )

    # ------------------------------------------------------------------
    # 10. Footer
    # ------------------------------------------------------------------
    footer = (
        '<footer class="px-4 md:px-6 py-6 text-xs" style="border-top:1px solid var(--border)">'
        f'<p style="color:var(--muted)">Data sources: OpenStreetMap · Open-Meteo · Jadrolinija · Wikipedia</p>'
        + (f'<p class="mt-1" style="color:var(--muted)">Generated: {h(generated_at)}</p>'
           if generated_at else "")
        + '<p class="mt-1"><a href="https://github.com/lavicitor/island-guide-scraper"'
        ' class="hover:underline" style="color:var(--accent)">Scraper on GitHub</a></p>'
        "</footer>"
    )

    # ------------------------------------------------------------------
    # Assemble
    # ------------------------------------------------------------------
    page = "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'  <title>{h(name)} Island Guide</title>',
        '  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">',
        '  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>',
        '  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>',
        '  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>',
        '  <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>',
        '  <script src="https://cdn.tailwindcss.com"></script>',
        "  <style>",
        COMMON_CSS,
        ISLAND_EXTRA_CSS,
        "  </style>",
        "</head>",
        '<body style="background:var(--bg);color:var(--text)">',
        header,
        '<div class="max-w-3xl mx-auto">',
        hero,
        season,
        getting_there_html,
        map_section,
        '</div>',
        '<div class="max-w-3xl mx-auto">',
        weather_html,
        poi_html,
        nearby_html,
        knowledge_html,
        footer,
        "</div>",
        "<script>",
        "lucide.createIcons();",
        map_js,
        chart_js,
        "function toggleCard(el) {",
        "  var d = el.querySelector('.card-detail');",
        "  var ch = el.querySelector('.chevron');",
        "  d.hidden = !d.hidden;",
        "  ch.textContent = d.hidden ? '\\u203a' : '\\u2304';",
        "}",
        "</script>",
        "</body>",
        "</html>",
    ])

    out_path = os.path.join("islands", f"{slug}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"\nWritten: {out_path} ({len(page):,} bytes)")


def write_index_cache(merged, slug):
    """Persist the subset of merged data needed by the index page."""
    os.makedirs("index_cache", exist_ok=True)
    identity = merged.get("identity", {})
    cache = {
        "slug": slug,
        "name": identity.get("name", ""),
        "region": identity.get("region", ""),
        "area_km2": identity.get("area_km2"),
        "car_free": identity.get("car_free", False),
        "best_months": merged.get("weather", {}).get("best_months", []),
        "description": identity.get("description", ""),
        "hero_image": identity.get("hero_image"),
        "hero_image_credit": identity.get("hero_image_credit"),
        "tagline": identity.get("tagline", ""),
        "best_for": identity.get("best_for", []),
    }
    cache_path = os.path.join("index_cache", f"{slug}.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"Written: {cache_path}")


# ---------------------------------------------------------------------------
# Wikipedia hero image fetch
# ---------------------------------------------------------------------------

def fetch_hero_image(island_name):
    """Fetch hero image URL from Wikipedia REST API at build time. Returns URL or None."""
    import urllib.parse
    encoded = urllib.parse.quote(island_name.replace(" ", "_"))
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "island-guide-scraper/1.0 (educational project)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            original = (data.get("originalimage") or {}).get("source")
            if original:
                return original
            return (data.get("thumbnail") or {}).get("source")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Index page generation
# ---------------------------------------------------------------------------

def generate_index_page():
    """Write index.html from all JSON files currently in index_cache/."""
    cache_dir = "index_cache"
    if not os.path.isdir(cache_dir):
        print("⚠ No index_cache/ directory — run --island first.", file=sys.stderr)
        return

    cache_files = sorted(f for f in os.listdir(cache_dir) if f.endswith(".json"))
    if not cache_files:
        print("⚠ index_cache/ is empty — run --island first.", file=sys.stderr)
        return

    islands_data = [load_json(os.path.join(cache_dir, f)) for f in cache_files]

    def h(s):
        if s is None:
            return ""
        return _html.escape(str(s))

    cards_html = ""
    hero_summary = []

    for island in islands_data:
        name = island.get("name", "")
        slug = island.get("slug", name.lower())
        region = island.get("region", "")
        car_free = island.get("car_free", False)
        area = island.get("area_km2")
        description = island.get("description", "")
        best_months = island.get("best_months", [])

        community_hero = island.get("hero_image")
        credit = island.get("hero_image_credit") or ""

        if community_hero:
            hero_url = community_hero
            hero_used = "community data"
        else:
            hero_url = fetch_hero_image(name)
            hero_used = "Wikipedia image" if hero_url else "gradient fallback"

        hero_summary.append((name, hero_used))

        img_style = (
            "width:100%;height:200px;object-fit:cover;object-position:center;"
            "border-radius:8px 8px 0 0;display:block"
        )
        if hero_url:
            img_tag = f'<img src="{h(hero_url)}" alt="{h(name)}" style="{img_style}">'
            if credit:
                credit_overlay = (
                    f'<span style="position:absolute;bottom:4px;left:8px;'
                    f'font-size:10px;color:rgba(255,255,255,0.8)">{h(credit)}</span>'
                )
                hero_block = (
                    '<div style="position:relative">'
                    + img_tag
                    + '<div style="position:absolute;bottom:0;left:0;right:0;height:40px;'
                    'border-radius:0 0 0 0;background:linear-gradient(to top,rgba(0,0,0,0.45),transparent)"></div>'
                    + credit_overlay
                    + "</div>"
                )
            else:
                hero_block = img_tag
        else:
            hero_block = (
                '<div style="height:200px;border-radius:8px 8px 0 0;'
                'background:linear-gradient(135deg,#0891B2 0%,#0E7490 100%)"></div>'
            )

        chip_row = ""
        if car_free:
            chip_row += (
                '<span class="text-xs px-2 py-0.5 rounded-full font-medium"'
                ' style="background:var(--accent);color:#fff">Car-free</span>'
            )
        if area is not None:
            chip_row += (
                f'<span class="text-xs px-2 py-0.5 rounded-full font-medium"'
                f' style="background:var(--surface);border:1px solid var(--border);'
                f'color:var(--muted)">{area} km²</span>'
            )
        for month in best_months[:2]:
            chip_row += (
                f'<span class="text-xs px-2 py-0.5 rounded-full font-medium"'
                f' style="background:color-mix(in srgb,var(--accent) 15%,var(--surface));'
                f'color:var(--accent)">{h(month)}</span>'
            )

        cards_html += (
            f'<a href="islands/{slug}.html" class="block no-underline"'
            ' style="border-radius:8px;border:1px solid var(--border);background:var(--bg);'
            'box-shadow:0 1px 3px rgba(0,0,0,0.08);transition:box-shadow 0.2s"'
            " onmouseover=\"this.style.boxShadow='0 4px 12px rgba(0,0,0,0.15)'\""
            " onmouseout=\"this.style.boxShadow='0 1px 3px rgba(0,0,0,0.08)'\">"
            + hero_block
            + '<div style="padding:1rem">'
            f'<p class="font-semibold" style="font-size:18px;color:var(--text)">{h(name)}</p>'
            f'<p class="text-xs mt-0.5" style="color:var(--muted)">{h(region)}</p>'
            f'<div class="flex flex-wrap gap-1 mt-2">{chip_row}</div>'
            f'<p class="text-sm mt-2" style="color:var(--muted);display:-webkit-box;'
            f'-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">{h(description)}</p>'
            '<p class="text-sm mt-3 font-medium" style="color:var(--accent)">View guide →</p>'
            "</div></a>"
        )

    footer_html = (
        '<footer style="border-top:1px solid var(--border);padding:1.5rem 1rem;'
        'max-width:1100px;margin:0 auto;font-size:0.75rem">'
        '<p style="color:var(--muted)">Data sources: OpenStreetMap · Open-Meteo · Jadrolinija · Wikipedia</p>'
        '<p class="mt-1"><a href="https://github.com/lavicitor/island-guide-scraper"'
        ' style="color:var(--accent)">Scraper on GitHub</a></p>'
        "</footer>"
    )

    page = "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        '  <title>Croatian Island Guides</title>',
        '  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">',
        '  <script src="https://cdn.tailwindcss.com"></script>',
        "  <style>",
        COMMON_CSS,
        "  </style>",
        "</head>",
        '<body style="background:var(--bg);color:var(--text)">',
        '<header class="sticky top-0 z-50 px-4 md:px-6 py-4"'
        ' style="background:var(--bg);border-bottom:1px solid var(--border)">',
        '<div style="max-width:1100px;margin:0 auto">',
        '<h1 class="font-bold text-xl" style="color:var(--text)">Croatian Island Guides</h1>',
        '<p class="text-sm mt-0.5" style="color:var(--muted)">Structured travel data for Croatia\'s islands</p>',
        "</div></header>",
        '<main style="max-width:1100px;margin:0 auto;padding:2rem 1rem">',
        '<div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">',
        cards_html,
        "</div></main>",
        footer_html,
        "</body>",
        "</html>",
    ])

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Written: index.html ({len(page):,} bytes)")

    for name, status in hero_summary:
        print(f"  Hero image ({name}): {status}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Merge island data and generate HTML pages.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--island", help="Island slug/name, e.g. Silba")
    group.add_argument("--rebuild-index", action="store_true",
                       help="Rebuild index.html from index_cache/ without re-processing islands")
    args = parser.parse_args()

    if args.rebuild_index:
        generate_index_page()
        return

    import unicodedata
    slug = "".join(
        c for c in unicodedata.normalize("NFD", args.island.lower())
        if unicodedata.category(c) != "Mn"
    )
    data_path = os.path.join("data", f"{slug}.json")
    community_path = os.path.join("community", f"{slug}.json")

    if not os.path.exists(data_path):
        print(f"Error: file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    data = load_json(data_path)

    if os.path.exists(community_path):
        community = load_json(community_path)
        merged = merge(data, community)
    else:
        print(f"⚠ No community data found for {args.island} — using scraped data only")
        merged = data

    summarise(merged)
    generate_island_page(merged, slug)
    write_index_cache(merged, slug)
    generate_index_page()


if __name__ == "__main__":
    main()
