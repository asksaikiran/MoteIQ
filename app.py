"""
MoteIQ Analytics Dashboard
===========================
Run with:  streamlit run app.py
Expects:   data/ folder with the 4 CSVs next to this file
           (daily_revenue.csv, guest_stays.csv, occupancy_stats.csv, transactions.csv)
"""

import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MoteIQ Analytics",
    page_icon="🏨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@300;400;500&display=swap');

  html, body, [class*="css"] {
      font-family: 'DM Sans', sans-serif;
  }

  /* Dark slate background */
  .stApp { background-color: #0f1117; }

  /* Sidebar */
  [data-testid="stSidebar"] {
      background-color: #161b27;
      border-right: 1px solid #2a3347;
  }

  /* Metric cards */
  [data-testid="stMetric"] {
      background: #161b27;
      border: 1px solid #2a3347;
      border-radius: 12px;
      padding: 16px 20px;
  }
  [data-testid="stMetricLabel"] { color: #8899aa !important; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; }
  [data-testid="stMetricValue"] { color: #e8edf5 !important; font-family: 'DM Serif Display', serif; font-size: 2rem; }
  [data-testid="stMetricDelta"] { font-size: 0.8rem; }

  /* Section headers */
  h1 { font-family: 'DM Serif Display', serif !important; color: #e8edf5 !important; }
  h2, h3 { font-family: 'DM Sans', sans-serif !important; color: #c5d0e0 !important; font-weight: 500 !important; }

  /* Plotly chart containers */
  .js-plotly-plot { border-radius: 12px; }

  /* Divider */
  hr { border-color: #2a3347; }

  /* Selectbox / slider labels */
  label { color: #8899aa !important; font-size: 0.8rem !important; }

  /* Tab styling */
  button[data-baseweb="tab"] {
      color: #8899aa !important;
      font-size: 0.85rem;
  }
  button[data-baseweb="tab"][aria-selected="true"] {
      color: #4fc3f7 !important;
      border-bottom-color: #4fc3f7 !important;
  }
</style>
""", unsafe_allow_html=True)

# ── Plotly theme ──────────────────────────────────────────────────────────────
PLOT_BG   = "#0f1117"
PAPER_BG  = "#161b27"
GRID_CLR  = "#2a3347"
TEXT_CLR  = "#8899aa"
ACCENT    = "#4fc3f7"
ACCENT2   = "#f06292"
ACCENT3   = "#a5d6a7"

LAYOUT_BASE = dict(
    paper_bgcolor=PAPER_BG,
    plot_bgcolor=PLOT_BG,
    font=dict(family="DM Sans", color=TEXT_CLR, size=12),
    margin=dict(l=16, r=16, t=40, b=16),
    xaxis=dict(gridcolor=GRID_CLR, showline=False, zeroline=False),
    yaxis=dict(gridcolor=GRID_CLR, showline=False, zeroline=False),
)

# ── Data loading ──────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"/ "processed"

@st.cache_resource
def get_conn():
    con = duckdb.connect()
    for csv_name, table in [
        ("daily_revenue.csv",   "daily_revenue"),
        ("guest_stays.csv",     "guest_stays"),
        ("occupancy_stats.csv", "occupancy_stats"),
        ("transactions.csv",    "transactions"),
    ]:
        path = DATA_DIR / csv_name
        if path.exists():
            con.execute(f"""
                CREATE TABLE {table} AS
                SELECT * FROM read_csv_auto('{path}', header=True)
            """)
        else:
            st.error(f"Missing file: {path}")
    return con

con = get_conn()

def q(sql: str) -> pd.DataFrame:
    return con.execute(sql).df()

# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏨 MoteIQ")
    st.markdown("---")

    years_df = q("""
        SELECT DISTINCT CAST(YEAR(TRY_STRPTIME(report_date, '%m/%d/%Y')) AS VARCHAR) AS yr
        FROM daily_revenue
        WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
        ORDER BY yr
    """)
    all_years = years_df["yr"].tolist()
    selected_years = st.multiselect("Filter by Year", all_years, default=all_years)

    year_filter = (
        "AND CAST(YEAR(TRY_STRPTIME(report_date, '%m/%d/%Y')) AS VARCHAR) IN ("
        + ",".join(f"'{y}'" for y in selected_years)
        + ")"
        if selected_years else ""
    )

    st.markdown("---")
    st.markdown(
        "<small style='color:#556677'>Built with DuckDB · Streamlit · Plotly</small>",
        unsafe_allow_html=True,
    )

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("# MoteIQ Analytics")
st.markdown(
    "<p style='color:#8899aa;margin-top:-12px;'>Night audit data · Baymont/Wyndham property</p>",
    unsafe_allow_html=True,
)

# ── KPI row ───────────────────────────────────────────────────────────────────
kpis = q(f"""
    SELECT
        ROUND(SUM(total_revenue), 0)            AS total_rev,
        ROUND(AVG(occupancy_pct), 1)            AS avg_occ,
        ROUND(AVG(adr), 2)                      AS avg_adr,
        COUNT(DISTINCT report_date)             AS days
    FROM daily_revenue
    WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
    {year_filter}
""")

guests_kpi = q(f"""
    SELECT COUNT(DISTINCT guest_id) AS unique_guests
    FROM guest_stays
    WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
    {year_filter}
""")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Revenue",    f"${kpis['total_rev'][0]:,.0f}")
c2.metric("Avg Occupancy",    f"{kpis['avg_occ'][0]:.1f}%")
c3.metric("Avg Daily Rate",   f"${kpis['avg_adr'][0]:.2f}")
c4.metric("Days of Data",     f"{kpis['days'][0]:,}")
c5.metric("Unique Guests",    f"{guests_kpi['unique_guests'][0]:,}")

st.markdown("---")

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📈 Revenue", "🛏 Occupancy", "👥 Guests", "💳 Transactions"])

# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Revenue
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    col_a, col_b = st.columns([2, 1])

    with col_a:
        monthly_rev = q(f"""
            SELECT
                DATE_TRUNC('month', TRY_STRPTIME(report_date, '%m/%d/%Y')) AS month,
                ROUND(SUM(total_revenue), 2)  AS revenue,
                ROUND(SUM(room_charges), 2)   AS room_charges,
                ROUND(SUM(taxes), 2)          AS taxes,
                ROUND(SUM(other_revenue), 2)  AS other
            FROM daily_revenue
            WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
            {year_filter}
            GROUP BY 1 ORDER BY 1
        """)
        monthly_rev["month"] = pd.to_datetime(monthly_rev["month"])

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=monthly_rev["month"], y=monthly_rev["room_charges"],
            name="Room Charges", marker_color="#4fc3f7", opacity=0.9
        ))
        fig.add_trace(go.Bar(
            x=monthly_rev["month"], y=monthly_rev["taxes"],
            name="Taxes", marker_color="#7986cb", opacity=0.9
        ))
        fig.add_trace(go.Bar(
            x=monthly_rev["month"], y=monthly_rev["other"],
            name="Other", marker_color="#f06292", opacity=0.9
        ))
        fig.update_layout(
            **LAYOUT_BASE,
            title="Monthly Revenue Breakdown",
            barmode="stack",
            legend=dict(orientation="h", y=1.1),
            height=360,
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        year_rev = q(f"""
            SELECT
                CAST(YEAR(TRY_STRPTIME(report_date, '%m/%d/%Y')) AS VARCHAR) AS year,
                ROUND(SUM(total_revenue), 0) AS revenue
            FROM daily_revenue
            WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
            {year_filter}
            GROUP BY 1 ORDER BY 1
        """)
        fig2 = px.bar(
            year_rev, x="year", y="revenue",
            color_discrete_sequence=[ACCENT],
            labels={"revenue": "Revenue ($)", "year": ""},
            title="Revenue by Year",
        )
        fig2.update_traces(marker_line_width=0)
        fig2.update_layout(**LAYOUT_BASE, height=360)
        st.plotly_chart(fig2, use_container_width=True)

    # ADR over time
    adr_trend = q(f"""
        SELECT
            DATE_TRUNC('month', TRY_STRPTIME(report_date, '%m/%d/%Y')) AS month,
            ROUND(AVG(adr), 2) AS avg_adr
        FROM daily_revenue
        WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
        {year_filter}
        GROUP BY 1 ORDER BY 1
    """)
    adr_trend["month"] = pd.to_datetime(adr_trend["month"])

    fig3 = px.line(
        adr_trend, x="month", y="avg_adr",
        labels={"avg_adr": "ADR ($)", "month": ""},
        title="Average Daily Rate (ADR) Trend",
        color_discrete_sequence=[ACCENT2],
    )
    fig3.update_traces(line_width=2.5)
    fig3.update_layout(**LAYOUT_BASE, height=280)
    st.plotly_chart(fig3, use_container_width=True)

# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Occupancy
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    occ_monthly = q(f"""
        SELECT
            DATE_TRUNC('month', TRY_STRPTIME(report_date, '%m/%d/%Y')) AS month,
            ROUND(AVG(CAST(rooms_occupied AS DOUBLE)), 1)  AS avg_rooms_occ,
            ROUND(AVG(CAST(vacant_rooms AS DOUBLE)), 1)    AS avg_vacant,
            ROUND(AVG(CAST(total_guests AS DOUBLE)), 1)    AS avg_guests,
            ROUND(AVG(CAST(walk_ins AS DOUBLE)), 1)        AS avg_walkins,
            ROUND(AVG(CAST(no_shows AS DOUBLE)), 1)        AS avg_noshows,
            ROUND(AVG(CAST(cancellations AS DOUBLE)), 1)   AS avg_cancels
        FROM occupancy_stats
        WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
        {year_filter}
        GROUP BY 1 ORDER BY 1
    """)
    occ_monthly["month"] = pd.to_datetime(occ_monthly["month"])

    col1, col2 = st.columns(2)
    with col1:
        fig_occ = go.Figure()
        fig_occ.add_trace(go.Scatter(
            x=occ_monthly["month"], y=occ_monthly["avg_rooms_occ"],
            name="Rooms Occupied", fill="tozeroy",
            line=dict(color=ACCENT, width=2),
            fillcolor="rgba(79,195,247,0.15)",
        ))
        fig_occ.add_trace(go.Scatter(
            x=occ_monthly["month"], y=occ_monthly["avg_guests"],
            name="Total Guests", line=dict(color=ACCENT3, width=2, dash="dot"),
        ))
        fig_occ.update_layout(**LAYOUT_BASE, title="Avg Rooms Occupied vs Guests (Monthly)", height=340)
        st.plotly_chart(fig_occ, use_container_width=True)

    with col2:
        fig_nosho = go.Figure()
        fig_nosho.add_trace(go.Bar(
            x=occ_monthly["month"], y=occ_monthly["avg_walkins"],
            name="Walk-ins", marker_color=ACCENT3
        ))
        fig_nosho.add_trace(go.Bar(
            x=occ_monthly["month"], y=occ_monthly["avg_noshows"],
            name="No-shows", marker_color=ACCENT2
        ))
        fig_nosho.add_trace(go.Bar(
            x=occ_monthly["month"], y=occ_monthly["avg_cancels"],
            name="Cancellations", marker_color="#ffcc80"
        ))
        fig_nosho.update_layout(
            **LAYOUT_BASE, title="Walk-ins / No-shows / Cancellations",
            barmode="group", height=340,
            legend=dict(orientation="h", y=1.12),
        )
        st.plotly_chart(fig_nosho, use_container_width=True)

    # Occupancy heatmap by month + year
    heatmap_df = q(f"""
        SELECT
            CAST(YEAR(TRY_STRPTIME(report_date, '%m/%d/%Y')) AS VARCHAR)  AS year,
            CAST(MONTH(TRY_STRPTIME(report_date, '%m/%d/%Y')) AS VARCHAR) AS month_num,
            ROUND(AVG(CAST(rooms_occupied AS DOUBLE)), 1) AS avg_rooms
        FROM occupancy_stats
        WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
        {year_filter}
        GROUP BY 1, 2
    """)
    month_labels = {
        "1":"Jan","2":"Feb","3":"Mar","4":"Apr","5":"May","6":"Jun",
        "7":"Jul","8":"Aug","9":"Sep","10":"Oct","11":"Nov","12":"Dec"
    }
    heatmap_df["month_label"] = heatmap_df["month_num"].map(month_labels)
    pivot = heatmap_df.pivot(index="year", columns="month_label", values="avg_rooms")
    # Reorder months
    ordered_cols = [m for m in month_labels.values() if m in pivot.columns]
    pivot = pivot[ordered_cols]

    fig_heat = px.imshow(
        pivot,
        color_continuous_scale=[[0,"#0f1117"],[0.5,"#1a4a6e"],[1,"#4fc3f7"]],
        labels=dict(color="Rooms"),
        title="Avg Rooms Occupied — Year × Month Heatmap",
        aspect="auto",
    )
    fig_heat.update_layout(**LAYOUT_BASE, height=280, coloraxis_showscale=True)
    st.plotly_chart(fig_heat, use_container_width=True)

# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — Guests
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    col1, col2 = st.columns(2)

    with col1:
        pay_methods = q(f"""
            SELECT
                CASE payment_method
                    WHEN 'VI' THEN 'Visa'
                    WHEN 'MC' THEN 'Mastercard'
                    WHEN 'AX' THEN 'Amex'
                    WHEN 'DS' THEN 'Discover'
                    WHEN 'CA' THEN 'Cash'
                    WHEN 'DB' THEN 'Direct Bill'
                    WHEN 'DR' THEN 'Debit'
                    WHEN 'TA' THEN 'Travel Agency'
                    ELSE COALESCE(NULLIF(payment_method,''), 'Unknown')
                END AS method,
                COUNT(*) AS stays
            FROM guest_stays
            WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
            {year_filter}
            GROUP BY 1 ORDER BY 2 DESC
            LIMIT 10
        """)
        fig_pay = px.pie(
            pay_methods, values="stays", names="method",
            title="Payment Methods",
            hole=0.45,
            color_discrete_sequence=px.colors.sequential.Blues_r,
        )
        fig_pay.update_traces(textfont_color="white")
        fig_pay.update_layout(**LAYOUT_BASE, height=340, showlegend=True,
                              legend=dict(font=dict(color="#8899aa")))
        st.plotly_chart(fig_pay, use_container_width=True)

    with col2:
        stay_len = q(f"""
            SELECT
                CAST(stay_length AS INTEGER) AS nights,
                COUNT(*) AS guests
            FROM guest_stays
            WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
              AND stay_length IS NOT NULL
              AND CAST(stay_length AS INTEGER) BETWEEN 1 AND 14
            {year_filter}
            GROUP BY 1 ORDER BY 1
        """)
        fig_stay = px.bar(
            stay_len, x="nights", y="guests",
            title="Stay Length Distribution (nights)",
            color_discrete_sequence=[ACCENT],
            labels={"nights": "Nights", "guests": "Guest Count"},
        )
        fig_stay.update_layout(**LAYOUT_BASE, height=340)
        st.plotly_chart(fig_stay, use_container_width=True)

    # Repeat guests
    repeat = q(f"""
        SELECT
            first_name || ' ' || last_name AS name,
            COUNT(DISTINCT report_date)    AS visits,
            MIN(TRY_STRPTIME(arrival)  AS first_stay,
            MAX(TRY_CAST(departure)) AS last_stay
        FROM guest_stays
        WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
          AND first_name != '' AND last_name != ''
        {year_filter}
        GROUP BY guest_id, name
        HAVING visits > 2
        ORDER BY visits DESC
        LIMIT 15
    """)
    st.markdown("### 🔁 Most Frequent Guests")
    st.dataframe(
        repeat.style.background_gradient(subset=["visits"], cmap="Blues"),
        use_container_width=True,
        hide_index=True,
    )

# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — Transactions
# ════════════════════════════════════════════════════════════════════════════
with tab4:
    col1, col2 = st.columns(2)

    with col1:
        txn_codes = q(f"""
            SELECT
                CASE txn_code
                    WHEN 'RM'   THEN 'Room Charge'
                    WHEN 'TAX1' THEN 'State Tax'
                    WHEN 'TAX2' THEN 'County Tax'
                    WHEN 'DR'   THEN 'Debit Card'
                    WHEN 'MC'   THEN 'Mastercard'
                    WHEN 'VI'   THEN 'Visa'
                    WHEN 'CA'   THEN 'Cash'
                    WHEN '1000' THEN 'Pet Fee'
                    ELSE COALESCE(NULLIF(txn_code,''), 'Other')
                END AS code_label,
                ROUND(SUM(amount), 2) AS total,
                COUNT(*)              AS count
            FROM transactions
            WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
              AND amount > 0
              AND txn_code NOT IN ('', '-')
            {year_filter}
            GROUP BY 1 ORDER BY total DESC
            LIMIT 10
        """)
        fig_txn = px.bar(
            txn_codes, x="total", y="code_label",
            orientation="h",
            title="Top Transaction Types by Revenue",
            color="total",
            color_continuous_scale=[[0,"#1a4a6e"],[1,"#4fc3f7"]],
            labels={"total": "Total ($)", "code_label": ""},
        )
        fig_txn.update_layout(**LAYOUT_BASE, height=380,
                              coloraxis_showscale=False, yaxis_autorange="reversed")
        st.plotly_chart(fig_txn, use_container_width=True)

    with col2:
        txn_monthly = q(f"""
            SELECT
                DATE_TRUNC('month', TRY_STRPTIME(report_date, '%m/%d/%Y')) AS month,
                ROUND(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 2) AS charges,
                ROUND(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END), 2) AS payments
            FROM transactions
            WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
            {year_filter}
            GROUP BY 1 ORDER BY 1
        """)
        txn_monthly["month"] = pd.to_datetime(txn_monthly["month"])

        fig_t2 = go.Figure()
        fig_t2.add_trace(go.Bar(
            x=txn_monthly["month"], y=txn_monthly["charges"],
            name="Charges", marker_color=ACCENT
        ))
        fig_t2.add_trace(go.Bar(
            x=txn_monthly["month"], y=txn_monthly["payments"].abs(),
            name="Payments / Credits", marker_color=ACCENT2
        ))
        fig_t2.update_layout(
            **LAYOUT_BASE, title="Monthly Charges vs Credits",
            barmode="group", height=380,
            legend=dict(orientation="h", y=1.12),
        )
        st.plotly_chart(fig_t2, use_container_width=True)

    # Net revenue check
    net = q(f"""
        SELECT ROUND(SUM(amount), 2) AS net_posted
        FROM transactions
        WHERE TRY_STRPTIME(report_date, '%m/%d/%Y') IS NOT NULL
        {year_filter}
    """)
    st.info(f"💡 Net posted transaction amount (charges minus credits): **${net['net_posted'][0]:,.2f}**")