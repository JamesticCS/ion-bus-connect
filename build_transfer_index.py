from pathlib import Path
import requests, zipfile, io, datetime as dt
import pandas as pd, geopandas as gpd
import shapely.ops as sops
import partridge as ptg
import folium

# Parameters
SERVICE_DATE = "2025-06-10"
PEAK_START = "07:00:00"
PEAK_END = "09:00:00"
BUFFER_METRES = 100
MAX_TRANSFER_M = 6
OUT_DIR = Path("output")

def download_gtfs(url, dest_path):
    try:
        print(f"Downloading GTFS from {url}...")
        response = requests.get(url)
        response.raise_for_status()
        dest_path.write_bytes(response.content)
        print(f"Downloaded to {dest_path}")
    except Exception as e:
        print(f"Error downloading GTFS: {e}")
        raise

def create_map(gdf, output_path):
    import branca.colormap as cm
    
    m = folium.Map(location=[gdf.geometry.y.mean(), gdf.geometry.x.mean()], zoom_start=12)
    
    # Create heatmap color scale (purple to dark red for better contrast)
    max_routes = gdf['bus_xfer_routes'].max()
    if max_routes > 0:
        colormap = cm.LinearColormap(
            colors=['#f1eef6', '#d4b9da', '#c994c7', '#df65b0', '#e7298a', '#ce1256', '#91003f', '#67001f'],
            vmin=0,
            vmax=max_routes,
            caption='Number of Bus Routes with Transfer Opportunities'
        )
        colormap.add_to(m)
    
    for _, row in gdf.iterrows():
        # Calculate color based on value
        if max_routes > 0 and row['bus_xfer_routes'] > 0:
            color = colormap(row['bus_xfer_routes'])
        else:
            color = '#505050'  # darker gray for 0 transfers
        
        # Scale radius based on value (min 6 for 0, then 8-20 for values > 0)
        if row['bus_xfer_routes'] == 0:
            radius = 6
        else:
            radius = 8 + (row['bus_xfer_routes'] / max(max_routes, 1)) * 12
        
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=radius,
            color='#000000',  # black border for all
            fill=True,
            fillColor=color,
            fillOpacity=0.9,  # same opacity for all
            weight=2,
            tooltip=f"{row['stop_name']}: {row['bus_xfer_routes']} bus routes"
        ).add_to(m)
    
    m.save(str(output_path))

def main():
    # Create directories
    Path("data").mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    
    # Download GTFS
    gtfs_zip = Path("data/grt_gtfs.zip")
    if not gtfs_zip.exists():
        download_gtfs("https://www.regionofwaterloo.ca/opendatadownloads/GRT_GTFS.zip", gtfs_zip)
    
    # Extract GTFS
    gtfs_dir = Path("data/gtfs")
    with zipfile.ZipFile(gtfs_zip, 'r') as zf:
        zf.extractall(gtfs_dir)
    
    # Load GTFS data manually to avoid partridge issues
    print("Loading GTFS data...")
    routes = pd.read_csv(gtfs_dir / 'routes.txt', dtype=str)
    trips = pd.read_csv(gtfs_dir / 'trips.txt', dtype=str)
    stop_times = pd.read_csv(gtfs_dir / 'stop_times.txt', dtype=str)
    calendar_dates = pd.read_csv(gtfs_dir / 'calendar_dates.txt', dtype=str)
    
    # Load stops with proper quoting
    stops = pd.read_csv(gtfs_dir / 'stops.txt', dtype=str, quotechar='"', on_bad_lines='skip')
    
    # Get service IDs for the target date
    target_date = SERVICE_DATE.replace('-', '')
    service_ids = calendar_dates[calendar_dates['date'] == target_date]['service_id'].unique()
    print(f"Found {len(service_ids)} service IDs for {SERVICE_DATE}")
    
    # Filter trips by service
    trips = trips[trips['service_id'].isin(service_ids)]
    
    # Identify ION and bus routes
    routes['route_type'] = routes['route_type'].astype(int)
    # ION light rail is route_type 2, route_id 301
    ion_routes = routes[routes['route_id'] == '301']['route_id'].tolist()
    bus_routes = routes[routes['route_type'] == 3]['route_id'].tolist()
    print(f"Found {len(ion_routes)} ION routes and {len(bus_routes)} bus routes")
    
    # Get trips for each mode
    ion_trips = trips[trips['route_id'].isin(ion_routes)]
    bus_trips = trips[trips['route_id'].isin(bus_routes)]
    
    # Get stop IDs for each mode
    ion_stop_ids = stop_times[stop_times['trip_id'].isin(ion_trips['trip_id'])]['stop_id'].unique()
    bus_stop_ids = stop_times[stop_times['trip_id'].isin(bus_trips['trip_id'])]['stop_id'].unique()
    print(f"Found {len(ion_stop_ids)} ION stops and {len(bus_stop_ids)} bus stops")
    
    # Create GeoDataFrames
    ion_stops_df = stops[stops['stop_id'].isin(ion_stop_ids)].copy()
    bus_stops_df = stops[stops['stop_id'].isin(bus_stop_ids)].copy()
    
    ion_stops = gpd.GeoDataFrame(
        ion_stops_df,
        geometry=gpd.points_from_xy(
            ion_stops_df['stop_lon'].astype(float),
            ion_stops_df['stop_lat'].astype(float)
        ),
        crs='EPSG:4326'
    ).to_crs(epsg=26917)
    
    bus_stops = gpd.GeoDataFrame(
        bus_stops_df,
        geometry=gpd.points_from_xy(
            bus_stops_df['stop_lon'].astype(float),
            bus_stops_df['stop_lat'].astype(float)
        ),
        crs='EPSG:4326'
    ).to_crs(epsg=26917)
    
    # Get timetables for peak period
    print("Filtering timetables for peak period...")
    st_with_routes = stop_times.merge(trips[['trip_id', 'route_id']], on='trip_id')
    
    # Filter for valid times and peak period
    mask = (st_with_routes['arrival_time'].notna() & 
            st_with_routes['arrival_time'].str.match(r'^\d{2}:\d{2}:\d{2}$', na=False) &
            (st_with_routes['arrival_time'] >= PEAK_START) & 
            (st_with_routes['arrival_time'] <= PEAK_END))
    ion_times = st_with_routes[st_with_routes['route_id'].isin(ion_routes) & mask].copy()
    
    mask = (st_with_routes['departure_time'].notna() & 
            st_with_routes['departure_time'].str.match(r'^\d{2}:\d{2}:\d{2}$', na=False) &
            (st_with_routes['departure_time'] >= PEAK_START) & 
            (st_with_routes['departure_time'] <= PEAK_END))
    bus_times = st_with_routes[st_with_routes['route_id'].isin(bus_routes) & mask].copy()
    
    print(f"Found {len(ion_times)} ION arrivals and {len(bus_times)} bus departures in peak period")
    
    # Spatial join to find nearby bus stops for each ION stop
    print("Finding nearby bus stops...")
    ion_buffers = ion_stops.copy()
    ion_buffers['geometry'] = ion_buffers.buffer(BUFFER_METRES)
    
    nearby = gpd.sjoin(bus_stops, ion_buffers, predicate='within', how='inner')
    
    # Calculate transfers
    print("Calculating transfer opportunities...")
    transfers = []
    
    for _, row in nearby.iterrows():
        ion_stop_id = row['stop_id_right']
        bus_stop_id = row['stop_id_left']
        
        # Get ION arrivals at this stop
        ion_arr = ion_times[ion_times['stop_id'] == ion_stop_id]
        # Get bus departures from nearby stop
        bus_dep = bus_times[bus_times['stop_id'] == bus_stop_id]
        
        if len(ion_arr) == 0 or len(bus_dep) == 0:
            continue
            
        # Find valid transfers
        for _, i_row in ion_arr.iterrows():
            ion_time = pd.to_datetime(i_row['arrival_time'], format='%H:%M:%S')
            for _, b_row in bus_dep.iterrows():
                bus_time = pd.to_datetime(b_row['departure_time'], format='%H:%M:%S')
                diff_minutes = (bus_time - ion_time).total_seconds() / 60
                
                if 0 <= diff_minutes <= MAX_TRANSFER_M:
                    transfers.append({
                        'ion_stop_id': ion_stop_id,
                        'bus_route_id': b_row['route_id']
                    })
    
    # Aggregate results
    if transfers:
        transfers_df = pd.DataFrame(transfers)
        xfer_counts = transfers_df.groupby('ion_stop_id')['bus_route_id'].nunique().reset_index()
        xfer_counts.columns = ['stop_id', 'bus_xfer_routes']
    else:
        xfer_counts = pd.DataFrame(columns=['stop_id', 'bus_xfer_routes'])
    
    # Prepare output
    ion_stops_wgs = ion_stops.to_crs(epsg=4326)
    output = ion_stops_wgs.merge(xfer_counts, on='stop_id', how='left')
    output['bus_xfer_routes'] = output['bus_xfer_routes'].fillna(0).astype(int)
    
    # Save outputs
    print("Saving outputs...")
    csv_cols = ['stop_id', 'stop_name', 'bus_xfer_routes']
    
    if len(output) > 0:
        output[csv_cols].to_csv(OUT_DIR / "ion_transfer_index.csv", index=False)
        output.to_file(OUT_DIR / "ion_transfer_index.geojson", driver='GeoJSON')
        create_map(output, OUT_DIR / "ion_transfer_map.html")
    else:
        print("WARNING: No ION stops found. Check if ION service runs on the selected date.")
        # Create empty outputs
        pd.DataFrame(columns=csv_cols).to_csv(OUT_DIR / "ion_transfer_index.csv", index=False)
    
    print(f"Analysis complete. Outputs in {OUT_DIR}/")
    print(f"Total ION stops analyzed: {len(output)}")
    print(f"ION stops with transfer opportunities: {(output['bus_xfer_routes'] > 0).sum()}")

if __name__ == "__main__":
    main()