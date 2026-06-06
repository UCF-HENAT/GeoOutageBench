from __future__ import annotations

import geopandas as gpd
import requests
import json
import time
import zipfile
import io
import urllib.request

# State FIPS to name mapping (copied from generate_counties_ttl.py)
STATE_FIPS_TO_NAME = {
    '01': 'Alabama', '02': 'Alaska', '04': 'Arizona', '05': 'Arkansas', '06': 'California',
    '08': 'Colorado', '09': 'Connecticut', '10': 'Delaware', '11': 'District of Columbia',
    '12': 'Florida', '13': 'Georgia', '15': 'Hawaii', '16': 'Idaho', '17': 'Illinois',
    '18': 'Indiana', '19': 'Iowa', '20': 'Kansas', '21': 'Kentucky', '22': 'Louisiana',
    '23': 'Maine', '24': 'Maryland', '25': 'Massachusetts', '26': 'Michigan', '27': 'Minnesota',
    '28': 'Mississippi', '29': 'Missouri', '30': 'Montana', '31': 'Nebraska', '32': 'Nevada',
    '33': 'New Hampshire', '34': 'New Jersey', '35': 'New Mexico', '36': 'New York',
    '37': 'North Carolina', '38': 'North Dakota', '39': 'Ohio', '40': 'Oklahoma', '41': 'Oregon',
    '42': 'Pennsylvania', '44': 'Rhode Island', '45': 'South Carolina', '46': 'South Dakota',
    '47': 'Tennessee', '48': 'Texas', '49': 'Utah', '50': 'Vermont', '51': 'Virginia',
    '53': 'Washington', '54': 'West Virginia', '55': 'Wisconsin', '56': 'Wyoming',
    '60': 'American Samoa', '66': 'Guam', '69': 'Northern Mariana Islands', '72': 'Puerto Rico',
    '74': 'U.S. Minor Outlying Islands', '78': 'U.S. Virgin Islands'
}

OTHER = [
    "Puerto Rico",
    "U.S. Virgin Islands",
    "District of Columbia",
]

def get_identifier(state_name: str) -> str:
    if state_name == "Louisiana":
        return "Parish"
    if state_name == "Alaska":
        return "Borough"
    if state_name == "American Samoa":
        return "District"
    if state_name == "Northern Mariana Islands":
        return "Municipality"
    if state_name in OTHER:
        return ""
    return "County"

def download_and_load_shapefile(url):
    """Download and load the county shapefile from TIGER."""
    print(f"Downloading shapefile from {url}...")
    with urllib.request.urlopen(url) as response:
        zip_content = response.read()
    
    with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
        # Find the .shp file
        shp_files = [f for f in zf.namelist() if f.endswith('.shp')]
        if not shp_files:
            raise ValueError("No .shp file found in the ZIP")
        
        shp_file = shp_files[0]
        print(f"Loading {shp_file}...")
        
        # Extract to memory
        with zf.open(shp_file) as shp_f:
            shp_data = shp_f.read()
        
        # Find corresponding files
        base_name = shp_file[:-4]  # remove .shp
        dbf_file = base_name + '.dbf'
        shx_file = base_name + '.shx'
        
        with zf.open(dbf_file) as dbf_f:
            dbf_data = dbf_f.read()
        with zf.open(shx_file) as shx_f:
            shx_data = shx_f.read()
        
        # Create in-memory files
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmpdir:
            shp_path = os.path.join(tmpdir, shp_file)
            dbf_path = os.path.join(tmpdir, dbf_file)
            shx_path = os.path.join(tmpdir, shx_file)
            
            with open(shp_path, 'wb') as f:
                f.write(shp_data)
            with open(dbf_path, 'wb') as f:
                f.write(dbf_data)
            with open(shx_path, 'wb') as f:
                f.write(shx_data)
            
            gdf = gpd.read_file(shp_path)
    
    return gdf

def get_wikidata_id(search_term):
    """Query Wikidata search API for the entity ID."""
    url = "https://www.wikidata.org/w/api.php"
    params = {
        "action": "wbsearchentities",
        "search": search_term,
        "language": "en",
        "format": "json"
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.5"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if "search" in data and data["search"]:
            return data["search"][0]["id"]  # Take the first result
        return None
    except requests.RequestException as e:
        print(f"Request failed for '{search_term}': {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON decode failed for '{search_term}': {e}, response text: {response.text[:200]}")
        return None

def main():
    # TIGER 2025 County Shapefile URL
    tiger_url = "https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/tl_2025_us_county.zip"
    
    # Load the shapefile
    gdf = download_and_load_shapefile(tiger_url)
    
    # Filter to continental US + territories if needed (optional)
    # gdf = gdf[~gdf['STATEFP'].isin(['02', '15', '60', '66', '69', '72', '74', '78'])]  # Exclude AK, HI, territories
    
    mapping = {}
    total = len(gdf)
    
    print(f"Processing {total} counties...")
    
    for idx, (_, row) in enumerate(gdf.iterrows()):
        name = row['NAME']
        statefp = row['STATEFP']
        geoid = row['GEOID']
        
        state_name = STATE_FIPS_TO_NAME.get(statefp, "")
        if not state_name:
            print(f"Unknown state FIPS: {statefp} for GEOID {geoid}")
            continue
        
        # Construct search term
        identifier = get_identifier(state_name)
        search_term = f"{name} {identifier}, {state_name}"
        
        wikidata_id = get_wikidata_id(search_term)
        
        if wikidata_id:
            mapping[geoid] = {"wikidata": wikidata_id}
            print(f"{idx+1}/{total}: {geoid} -> {wikidata_id}")
        else:
            print(f"{idx+1}/{total}: {geoid} ({search_term}) -> Not found")
        
        time.sleep(10)  # Rate limiting
    
    # Save to JSON
    with open("wikidata_mapping.json", "w") as f:
        json.dump(mapping, f, indent=2)
    
    print(f"Mapping saved to wikidata_mapping.json with {len(mapping)} entries")

if __name__ == "__main__":
    main()