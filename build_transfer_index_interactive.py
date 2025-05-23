from pathlib import Path
import requests, zipfile, io, datetime as dt
import pandas as pd, geopandas as gpd
import numpy as np
import folium
from folium import plugins
import json

# Parameters
SERVICE_DATE = "2025-06-10"
PEAK_START = "07:00:00"
PEAK_END = "09:00:00"
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

def calculate_transfers_for_distance(ion_stops, bus_stops, ion_times, bus_times, buffer_m):
    """Calculate transfer opportunities for a specific buffer distance"""
    # Buffer ION stops
    ion_buffers = ion_stops.copy()
    ion_buffers['geometry'] = ion_buffers.buffer(buffer_m)
    
    # Spatial join
    nearby = gpd.sjoin(bus_stops, ion_buffers, predicate='within', how='inner')
    
    # Calculate transfers
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
    result = pd.DataFrame(index=ion_stops['stop_id'])
    result['bus_xfer_routes'] = 0
    
    if transfers:
        transfers_df = pd.DataFrame(transfers)
        xfer_counts = transfers_df.groupby('ion_stop_id')['bus_route_id'].nunique()
        result.loc[xfer_counts.index, 'bus_xfer_routes'] = xfer_counts.values
    
    return result['bus_xfer_routes'].to_dict()

def create_interactive_map(ion_stops_wgs, transfer_data_by_distance, output_path):
    """Create map with interactive distance slider"""
    import branca
    
    m = folium.Map(location=[ion_stops_wgs.geometry.y.mean(), ion_stops_wgs.geometry.x.mean()], zoom_start=12)
    
    # Prepare data for JavaScript
    stops_data = []
    for _, stop in ion_stops_wgs.iterrows():
        stop_transfers = {}
        for dist, transfers in transfer_data_by_distance.items():
            stop_transfers[dist] = transfers.get(stop['stop_id'], 0)
        
        stops_data.append({
            'stop_id': stop['stop_id'],
            'stop_name': stop['stop_name'],
            'lat': stop.geometry.y,
            'lon': stop.geometry.x,
            'transfers': stop_transfers
        })
    
    # Add JavaScript and HTML for slider
    html = """
    <div style='position: fixed; top: 10px; right: 10px; width: 300px; background: white; 
                padding: 10px; border: 2px solid grey; z-index: 1000; border-radius: 5px;'>
        <h4 style='margin-top: 0;'>Transfer Distance</h4>
        <input type='range' id='distanceSlider' min='50' max='500' value='100' step='50' 
               style='width: 100%;'>
        <div style='text-align: center; margin-top: 5px;'>
            <span id='distanceValue'>100</span> meters
        </div>
    </div>
    """
    
    js = f"""
    <script>
    var stopsData = {json.dumps(stops_data)};
    var markers = [];
    var colorScale = ['#f1eef6', '#d4b9da', '#c994c7', '#df65b0', '#e7298a', '#ce1256', '#91003f', '#67001f'];
    
    function getColor(value, maxValue) {{
        if (value === 0) return '#505050';
        if (maxValue === 0) return '#505050';
        var index = Math.floor((value / maxValue) * (colorScale.length - 1));
        return colorScale[Math.min(index, colorScale.length - 1)];
    }}
    
    function updateMap(distance) {{
        // Clear existing markers
        markers.forEach(function(marker) {{
            marker.remove();
        }});
        markers = [];
        
        // Find max value for current distance
        var maxValue = 0;
        stopsData.forEach(function(stop) {{
            if (stop.transfers[distance] > maxValue) {{
                maxValue = stop.transfers[distance];
            }}
        }});
        
        // Add new markers
        stopsData.forEach(function(stop) {{
            var value = stop.transfers[distance];
            var color = getColor(value, maxValue);
            var radius = value === 0 ? 6 : 8 + (value / Math.max(maxValue, 1)) * 12;
            
            var marker = L.circleMarker([stop.lat, stop.lon], {{
                radius: radius,
                color: '#000000',
                fillColor: color,
                fillOpacity: 0.9,
                weight: 2
            }}).addTo({m.get_name()});
            
            marker.bindTooltip(stop.stop_name + ': ' + value + ' bus routes');
            markers.push(marker);
        }});
    }}
    
    // Initialize with 100m
    updateMap(100);
    
    // Add slider event listener
    document.getElementById('distanceSlider').addEventListener('input', function(e) {{
        var distance = parseInt(e.target.value);
        document.getElementById('distanceValue').textContent = distance;
        updateMap(distance);
    }});
    </script>
    """
    
    # Add HTML and JavaScript to map
    m.get_root().html.add_child(folium.Element(html))
    m.get_root().html.add_child(folium.Element(js))
    
    # Add color legend
    colormap_html = """
    <div style='position: fixed; bottom: 30px; left: 10px; width: 200px; background: white; 
                padding: 10px; border: 2px solid grey; z-index: 1000; border-radius: 5px;
                font-size: 12px;'>
        <div style='font-weight: bold; margin-bottom: 5px;'>Bus Transfer Routes</div>
        <div style='background: linear-gradient(to right, #505050 0%, #505050 10%, 
                    #f1eef6 10%, #67001f 100%); height: 20px; margin: 5px 0;'></div>
        <div style='display: flex; justify-content: space-between;'>
            <span>0</span>
            <span>Max</span>
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(colormap_html))
    
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
    
    # Load GTFS data
    print("Loading GTFS data...")
    routes = pd.read_csv(gtfs_dir / 'routes.txt', dtype=str)
    trips = pd.read_csv(gtfs_dir / 'trips.txt', dtype=str)
    stop_times = pd.read_csv(gtfs_dir / 'stop_times.txt', dtype=str)
    calendar_dates = pd.read_csv(gtfs_dir / 'calendar_dates.txt', dtype=str)
    stops = pd.read_csv(gtfs_dir / 'stops.txt', dtype=str, quotechar='"', on_bad_lines='skip')
    
    # Get service IDs for the target date
    target_date = SERVICE_DATE.replace('-', '')
    service_ids = calendar_dates[calendar_dates['date'] == target_date]['service_id'].unique()
    print(f"Found {len(service_ids)} service IDs for {SERVICE_DATE}")
    
    # Filter trips by service
    trips = trips[trips['service_id'].isin(service_ids)]
    
    # Identify ION and bus routes
    routes['route_type'] = routes['route_type'].astype(int)
    ion_routes = routes[routes['route_id'] == '301']['route_id'].tolist()
    bus_routes = routes[routes['route_type'] == 3]['route_id'].tolist()
    
    # Get trips for each mode
    ion_trips = trips[trips['route_id'].isin(ion_routes)]
    bus_trips = trips[trips['route_id'].isin(bus_routes)]
    
    # Get stop IDs for each mode
    ion_stop_ids = stop_times[stop_times['trip_id'].isin(ion_trips['trip_id'])]['stop_id'].unique()
    bus_stop_ids = stop_times[stop_times['trip_id'].isin(bus_trips['trip_id'])]['stop_id'].unique()
    
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
    
    # Calculate transfers for different distances
    print("Calculating transfers for different distances...")
    distances = range(50, 501, 50)
    transfer_data = {}
    
    for dist in distances:
        print(f"  Processing {dist}m...")
        transfer_data[dist] = calculate_transfers_for_distance(
            ion_stops, bus_stops, ion_times, bus_times, dist
        )
    
    # Create interactive map
    print("Creating interactive map...")
    ion_stops_wgs = ion_stops.to_crs(epsg=4326)
    create_interactive_map(ion_stops_wgs, transfer_data, OUT_DIR / "ion_transfer_map_interactive.html")
    
    print(f"Interactive map saved to {OUT_DIR}/ion_transfer_map_interactive.html")

if __name__ == "__main__":
    main()