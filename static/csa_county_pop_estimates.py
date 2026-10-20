"""
Annual county population for every county in a Combined Statistical Area
(default: New York-Newark, CSA 408), from the Census Bureau's Population Estimates
Program, Vintage 2025 (July 1 estimates, 2020-2025), plus July 1, 2019 from the
2010-2020 intercensal estimates (revised to be consistent with the 2020 census).

The intercensal county file still uses Connecticut's eight legacy counties, so for
Connecticut planning regions the 2019 value is the sum of the intercensal 2019
estimates of the towns in each region (town -> region list is built in below).

If a county has no 2020 estimate, the script falls back to its 2020 decennial
census count (April 1, 2020, table P1 from the redistricting file). If that isn't
available either (e.g. Connecticut planning regions, which postdate the census),
it uses the estimates base, which is built from the 2020 census.

Requirements:  pip install pandas openpyxl requests
Usage:         python csa_county_pop_estimates.py
               python csa_county_pop_estimates.py --csa 548
               set CENSUS_API_KEY=... (optional, only used for the decennial fallback)

Outputs:       county_pop_estimates_csa<code>.csv        one row per county-year (+ CSA totals)
               county_pop_estimates_csa<code>_wide.csv   one row per county, one column per year
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DELINEATION_URL = ("https://www2.census.gov/programs-surveys/metro-micro/geographies/"
                   "reference-files/2023/delineation-files/list1_2023.xlsx")
PEP_URL = ("https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/"
           "counties/totals/co-est2025-alldata.csv")
DEC_URL = "https://api.census.gov/data/2020/dec/pl"
INT_COUNTY_URL = ("https://www2.census.gov/programs-surveys/popest/tables/2010-2020/"
                  "intercensal/county/co-est2020int-pop.xlsx")
INT_CT_TOWN_URL = ("https://www2.census.gov/programs-surveys/popest/tables/2010-2020/"
                   "intercensal/cities/sub-mcd-est2020int-pop-09.xlsx")
YEARS = range(2020, 2026)
YEAR_2019 = 2019
SOURCE_2019 = "2010-2020 intercensal July 1 estimate"
SOURCE_2019_CT = "2010-2020 intercensal July 1 estimate (sum of towns)"
CACHE = Path("cache")


def fetch(url, name):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / name
    if not path.exists():
        print(f"  downloading {name} ...")
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


def load_csa_counties(csa_code):
    raw = pd.read_excel(fetch(DELINEATION_URL, "list1_2023.xlsx"), header=None, dtype=str)
    hdr = raw.index[raw.apply(lambda r: r.astype(str).str.contains("CSA Code").any(), axis=1)][0]
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr]]
    df = df[df["CSA Code"].astype(str).str.strip() == str(csa_code)].copy()
    if df.empty:
        sys.exit(f"No counties found for CSA {csa_code}.")
    df["geoid"] = df["FIPS State Code"].str.zfill(2) + df["FIPS County Code"].str.zfill(3)
    return df.rename(columns={"CBSA Title": "cbsa", "CSA Title": "csa"})[["geoid", "cbsa", "csa"]]


def load_pep():
    df = pd.read_csv(fetch(PEP_URL, "co-est2025-alldata.csv"), dtype=str, encoding="latin-1")
    df = df[df["SUMLEV"] == "050"].copy()
    df["geoid"] = df["STATE"].str.zfill(2) + df["COUNTY"].str.zfill(3)
    keep = ["geoid", "CTYNAME", "STNAME", "ESTIMATESBASE2020"] + \
           [f"POPESTIMATE{y}" for y in YEARS if f"POPESTIMATE{y}" in df.columns]
    df = df[keep].rename(columns={"CTYNAME": "county", "STNAME": "state"})
    for c in keep[3:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def decennial_2020(geoids, key):
    """2020 Census P1 total population for the given counties, as {geoid: count}."""
    out = {}
    for st in sorted({g[:2] for g in geoids}):
        params = {"get": "P1_001N", "for": "county:*", "in": f"state:{st}"}
        if key:
            params["key"] = key
        r = requests.get(DEC_URL, params=params, timeout=120)
        r.raise_for_status()
        rows = r.json()
        for pop, s, c in rows[1:]:
            out[s + c] = float(pop)
    return {g: out[g] for g in geoids if g in out}


def read_intercensal_table(path, year=YEAR_2019):
    """Read a Census intercensal .xlsx table -> {area name: estimate for `year`}.

    Tables have a title block, a header row whose cells include the years
    (2010 ... 2019), then rows like '.Bergen County, New Jersey' with the name in
    the first column."""
    raw = pd.read_excel(path, header=None)
    col = None
    for i in range(min(15, len(raw))):
        for j, v in enumerate(raw.iloc[i]):
            try:
                if int(float(str(v).strip())) == year:
                    col, hdr = j, i
                    break
            except ValueError:
                continue
        if col is not None:
            break
    if col is None:
        sys.exit(f"Couldn't find a {year} column in {path.name}.")
    out = {}
    for _, row in raw.iloc[hdr + 1:].iterrows():
        name, val = row.iloc[0], pd.to_numeric(row.iloc[col], errors="coerce")
        if isinstance(name, str) and pd.notna(val):
            out[name.strip().lstrip(".").strip()] = float(val)
    return out


# Connecticut's 169 towns by planning region (county equivalents since 2022)
CT_REGIONS = {
    "09110": "Andover, Avon, Berlin, Bloomfield, Bolton, Canton, Columbia, Coventry, East Granby, "
             "East Hartford, East Windsor, Ellington, Enfield, Farmington, Glastonbury, Granby, "
             "Hartford, Hebron, Manchester, Mansfield, Marlborough, New Britain, Newington, "
             "Plainville, Rocky Hill, Simsbury, Somers, South Windsor, Southington, Stafford, "
             "Suffield, Tolland, Vernon, West Hartford, Wethersfield, Willington, Windsor, "
             "Windsor Locks",
    "09120": "Bridgeport, Easton, Fairfield, Monroe, Stratford, Trumbull",
    "09130": "Chester, Clinton, Cromwell, Deep River, Durham, East Haddam, East Hampton, Essex, "
             "Haddam, Killingworth, Lyme, Middlefield, Middletown, Old Lyme, Old Saybrook, "
             "Portland, Westbrook",
    "09140": "Ansonia, Beacon Falls, Bethlehem, Bristol, Cheshire, Derby, Middlebury, Naugatuck, "
             "Oxford, Plymouth, Prospect, Seymour, Shelton, Southbury, Thomaston, Waterbury, "
             "Watertown, Wolcott, Woodbury",
    "09150": "Ashford, Brooklyn, Canterbury, Chaplin, Eastford, Hampton, Killingly, Plainfield, "
             "Pomfret, Putnam, Scotland, Sterling, Thompson, Union, Woodstock",
    "09160": "Barkhamsted, Burlington, Canaan, Colebrook, Cornwall, Goshen, Hartland, Harwinton, "
             "Kent, Litchfield, Morris, New Hartford, Norfolk, North Canaan, Roxbury, Salisbury, "
             "Sharon, Torrington, Warren, Washington, Winchester",
    "09170": "Bethany, Branford, East Haven, Guilford, Hamden, Madison, Meriden, Milford, "
             "New Haven, North Branford, North Haven, Orange, Wallingford, West Haven, Woodbridge",
    "09180": "Bozrah, Colchester, East Lyme, Franklin, Griswold, Groton, Lebanon, Ledyard, Lisbon, "
             "Montville, New London, North Stonington, Norwich, Preston, Salem, Sprague, "
             "Stonington, Voluntown, Waterford, Windham",
    "09190": "Bethel, Bridgewater, Brookfield, Danbury, Darien, Greenwich, New Canaan, "
             "New Fairfield, New Milford, Newtown, Norwalk, Redding, Ridgefield, Sherman, "
             "Stamford, Weston, Westport, Wilton",
}


def ct_town_regions():
    """{town name: planning-region geoid} for Connecticut, e.g. 'Bethel' -> '09190'."""
    return {t.strip(): g for g, towns in CT_REGIONS.items() for t in towns.split(",")}


def _town(name):
    """'Bethel town, Fairfield County, Connecticut' -> 'Bethel'."""
    t = name.split(",")[0].strip()
    return t[:-5] if t.endswith(" town") else t


def intercensal_2019(pep, key):
    """2019 population for the counties in `pep`, as {geoid: (value, source)}."""
    out = {}
    county_vals = read_intercensal_table(fetch(INT_COUNTY_URL, "co-est2020int-pop.xlsx"))
    for g, c, s in pep[["geoid", "county", "state"]].itertuples(index=False):
        v = county_vals.get(f"{c}, {s}")
        if v is not None:
            out[g] = (v, SOURCE_2019)

    # Connecticut planning regions: aggregate towns
    ct = [g for g in pep["geoid"] if g.startswith("09") and g not in out]
    if ct:
        towns = read_intercensal_table(fetch(INT_CT_TOWN_URL, "sub-mcd-est2020int-pop-09.xlsx"))
        # names look like 'Bethel town, Fairfield County, Connecticut'
        towns = {_town(n): v for n, v in towns.items()
                 if n.endswith(", Connecticut") and n.count(",") == 2}
        region_of = ct_town_regions()
        unmatched = sorted(set(towns) ^ set(region_of))
        if unmatched:
            print(f"  warning: CT towns not matched between sources: {', '.join(unmatched)}")
        sums = {}
        for t, v in towns.items():
            if t in region_of:
                sums[region_of[t]] = sums.get(region_of[t], 0) + v
        for g in ct:
            if g in sums:
                out[g] = (sums[g], SOURCE_2019_CT)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csa", default="408")
    ap.add_argument("--key", default=os.environ.get("CENSUS_API_KEY"))
    args = ap.parse_args()

    counties = load_csa_counties(args.csa)
    csa_name = counties["csa"].iloc[0]
    print(f"{csa_name}: {len(counties)} counties/equivalents")

    pep = load_pep().merge(counties[["geoid", "cbsa"]], on="geoid", how="inner")
    absent = set(counties.geoid) - set(pep.geoid)
    if absent:
        print(f"  warning: not found in the estimates file: {', '.join(sorted(absent))}")

    # 2020 fallback chain: July 1 estimate -> April 1 census count -> estimates base
    if "POPESTIMATE2020" not in pep.columns:
        pep["POPESTIMATE2020"] = np.nan
    pep["source_2020"] = np.where(pep["POPESTIMATE2020"].notna(), "PEP V2025 July 1 estimate", "")
    need = pep.loc[pep["POPESTIMATE2020"].isna(), "geoid"].tolist()
    if need:
        print(f"  {len(need)} counties lack a 2020 estimate; using the 2020 census instead")
        dec = decennial_2020(need, args.key)
        m = pep["geoid"].isin(dec)
        pep.loc[m, "POPESTIMATE2020"] = pep.loc[m, "geoid"].map(dec)
        pep.loc[m, "source_2020"] = "2020 Census (April 1)"
        m = pep["POPESTIMATE2020"].isna()
        pep.loc[m, "POPESTIMATE2020"] = pep.loc[m, "ESTIMATESBASE2020"]
        pep.loc[m, "source_2020"] = "Estimates base (2020 census-based, April 1)"

    # 2019 from the 2010-2020 intercensal estimates
    v19 = intercensal_2019(pep, args.key)
    pep[f"POPESTIMATE{YEAR_2019}"] = pep["geoid"].map({g: v for g, (v, _) in v19.items()})
    pep["source_2019"] = pep["geoid"].map({g: s for g, (_, s) in v19.items()}).fillna("")
    miss = pep.loc[pep[f"POPESTIMATE{YEAR_2019}"].isna(), "county"].tolist()
    if miss:
        print(f"  warning: no 2019 estimate for: {', '.join(miss)} (CSA 2019 total will be short)")

    # Long format
    year_cols = sorted(c for c in pep.columns if c.startswith("POPESTIMATE"))
    long = pep.melt(id_vars=["geoid", "county", "state", "cbsa", "source_2019", "source_2020"],
                    value_vars=year_cols, var_name="year", value_name="population")
    long["year"] = long["year"].str[-4:].astype(int)
    long["source"] = np.select([long["year"] == YEAR_2019, long["year"] == 2020],
                               [long["source_2019"], long["source_2020"]],
                               "PEP V2025 July 1 estimate")
    long["geography"] = "county"

    totals = long.groupby("year", as_index=False)["population"].sum()
    totals = totals.assign(geoid=f"CSA{args.csa}", county=csa_name, state="", cbsa="",
                           source="Sum of counties", geography="CSA total")
    long = pd.concat([long, totals], ignore_index=True)

    long = long.sort_values(["geoid", "year"])
    prev = long.groupby("geoid")["population"].shift(1)
    long["prev_year"] = long.groupby("geoid")["year"].shift(1).astype("Int64")
    long["change"] = long["population"] - prev
    long["pct_change"] = (100 * long["change"] / prev).round(2)
    long["population"] = long["population"].round().astype("Int64")
    long["change"] = long["change"].round().astype("Int64")

    cols = ["year", "geoid", "county", "state", "cbsa", "geography", "population",
            "prev_year", "change", "pct_change", "source"]
    out = f"county_pop_estimates_csa{args.csa}.csv"
    long[cols].sort_values(["year", "geography", "geoid"], ascending=[True, False, True]) \
        .to_csv(out, index=False)

    wide = long.pivot_table(index=["geoid", "county", "state", "geography"], columns="year",
                            values="population", aggfunc="first").reset_index()
    wide.columns = [str(c) for c in wide.columns]
    wide.to_csv(f"county_pop_estimates_csa{args.csa}_wide.csv", index=False)

    t = totals.set_index("year")["population"]
    print(f"\nCSA total: {t.iloc[0]:,.0f} ({t.index[0]}) -> {t.iloc[-1]:,.0f} ({t.index[-1]}), "
          f"{100 * (t.iloc[-1] / t.iloc[0] - 1):+.2f}%")
    print(f"Wrote {out} and the wide version.")


if __name__ == "__main__":
    main()
