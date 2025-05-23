#!/usr/bin/env python3
"""
ION-Bus Connect: Transit Transfer Analysis Tool

Analyzes transfer opportunities between ION light rail and bus routes
in Waterloo Region during specified time periods.

Author: ION-Bus Connect Contributors
License: MIT
"""

from pathlib import Path
import argparse
import sys
import requests, zipfile, io, datetime as dt
import pandas as pd, geopandas as gpd
import shapely.ops as sops
import partridge as ptg
import folium
import branca.colormap as cm
import json

# Default configuration
DEFAULT_GTFS_URL = "https://www.regionofwaterloo.ca/opendatadownloads/GRT_GTFS.zip"
DEFAULT_SERVICE_DATE = "2025-06-10"
DEFAULT_PEAK_START = "07:00:00"
DEFAULT_PEAK_END = "09:00:00"
DEFAULT_BUFFER_METRES = 100
DEFAULT_MAX_TRANSFER_MINUTES = 6


def download_gtfs(url, dest_path, force=False):
    """
    Download GTFS data from specified URL.
    
    Args:
        url: URL of GTFS zip file
        dest_path: Path to save the downloaded file
        force: Force re-download even if file exists
    """
    if dest_path.exists() and not force:
        print(f"✓ Using existing GTFS data at {dest_path}")
        return
        
    try:
        print(f"⬇️  Downloading GTFS data from {url}...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(response.content)
        print(f"✓ Downloaded to {dest_path}")
    except Exception as e:
        print(f"✗ Error downloading GTFS: {e}")
        raise


def create_map(gdf, output_path, buffer_metres):
    """
    Create an interactive Folium map showing transfer opportunities.
    
    Args:
        gdf: GeoDataFrame with ION stops and transfer counts
        output_path: Path to save the HTML map
        buffer_metres: Walking distance used in analysis
    """
    # Calculate map center
    center_lat = gdf.geometry.y.mean()
    center_lon = gdf.geometry.x.mean()
    
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)
    
    # Create color scale
    max_routes = gdf['bus_xfer_routes'].max()
    if max_routes > 0:
        colormap = cm.LinearColormap(
            colors=['#f1eef6', '#d4b9da', '#c994c7', '#df65b0', '#e7298a', '#ce1256', '#91003f', '#67001f'],
            vmin=0,
            vmax=max_routes,
            caption=f'Bus Routes with Transfer Opportunities (within {buffer_metres}m)'
        )
        colormap.add_to(m)
    
    # Add markers for each ION stop
    for _, row in gdf.iterrows():
        # Determine color
        if max_routes > 0 and row['bus_xfer_routes'] > 0:
            color = colormap(row['bus_xfer_routes'])
        else:
            color = '#505050'  # gray for 0 transfers
        
        # Scale radius (min 6 for 0, then 8-20 for values > 0)
        if row['bus_xfer_routes'] == 0:
            radius = 6
        else:
            radius = 8 + (row['bus_xfer_routes'] / max(max_routes, 1)) * 12
        
        # Create marker
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=radius,
            color='#000000',  # black border
            fill=True,
            fillColor=color,
            fillOpacity=0.9,
            weight=2,
            tooltip=f"{row['stop_name']}: {row['bus_xfer_routes']} bus routes"
        ).add_to(m)
    
    # Save map
    m.save(str(output_path))
    print(f"✓ Map saved to {output_path}")


def main():
    """Main analysis function."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Analyze ION-bus transfer opportunities in Waterloo Region',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Use all defaults
  %(prog)s --date 2025-07-15       # Analyze a different date
  %(prog)s --buffer 200            # Use 200m walking distance
  %(prog)s --time 17:00 19:00      # Evening peak analysis
        """
    )
    
    parser.add_argument('--date', default=DEFAULT_SERVICE_DATE,
                        help=f'Service date to analyze (default: {DEFAULT_SERVICE_DATE})')
    parser.add_argument('--time', nargs=2, default=[DEFAULT_PEAK_START, DEFAULT_PEAK_END],
                        metavar=('START', 'END'),
                        help='Time window for analysis (default: 07:00:00 09:00:00)')
    parser.add_argument('--buffer', type=int, default=DEFAULT_BUFFER_METRES,
                        help=f'Walking distance in metres (default: {DEFAULT_BUFFER_METRES})')
    parser.add_argument('--transfer-time', type=int, default=DEFAULT_MAX_TRANSFER_MINUTES,
                        help=f'Max transfer time in minutes (default: {DEFAULT_MAX_TRANSFER_MINUTES})')
    parser.add_argument('--gtfs-url', default=DEFAULT_GTFS_URL,
                        help='URL to download GTFS data')
    parser.add_argument('--force-download', action='store_true',
                        help='Force re-download of GTFS data')
    parser.add_argument('--output-dir', type=Path, default=Path("output"),
                        help='Output directory (default: output/)')
    
    args = parser.parse_args()
    
    # Validate arguments
    try:
        dt.date.fromisoformat(args.date)
    except ValueError:
        parser.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD")
    
    # Display configuration
    print("🚊 ION-Bus Connect - Transfer Analysis Tool")
    print("=" * 50)
    print(f"📅 Service date: {args.date}")
    print(f"⏰ Time window: {args.time[0]} - {args.time[1]}")
    print(f"🚶 Walking distance: {args.buffer}m")
    print(f"⏱️  Max transfer time: {args.transfer_time} minutes")
    print("=" * 50)
    
    # Create directories
    Path("data").mkdir(exist_ok=True)
    args.output_dir.mkdir(exist_ok=True)
    
    # Download GTFS data
    gtfs_zip = Path("data/grt_gtfs.zip")
    download_gtfs(args.gtfs_url, gtfs_zip, args.force_download)
    
    # Extract GTFS
    print("📂 Extracting GTFS data...")
    gtfs_dir = Path("data/gtfs")
    with zipfile.ZipFile(gtfs_zip, 'r') as zf:
        zf.extractall(gtfs_dir)
    
    # Load GTFS data
    print("📊 Loading transit data...")
    routes = pd.read_csv(gtfs_dir / 'routes.txt', dtype=str)
    trips = pd.read_csv(gtfs_dir / 'trips.txt', dtype=str)
    stop_times = pd.read_csv(gtfs_dir / 'stop_times.txt', dtype=str)
    calendar_dates = pd.read_csv(gtfs_dir / 'calendar_dates.txt', dtype=str)
    stops = pd.read_csv(gtfs_dir / 'stops.txt', dtype=str, quotechar='"', on_bad_lines='skip')
    
    # Get service IDs for target date
    target_date = args.date.replace('-', '')
    service_ids = calendar_dates[calendar_dates['date'] == target_date]['service_id'].unique()
    print(f"✓ Found {len(service_ids)} service IDs for {args.date}")
    
    if len(service_ids) == 0:
        print("✗ No service found for this date. It might be a holiday or non-service day.")
        sys.exit(1)
    
    # Filter trips by service
    trips = trips[trips['service_id'].isin(service_ids)]
    
    # Identify ION and bus routes
    routes['route_type'] = routes['route_type'].astype(int)
    ion_routes = routes[routes['route_id'] == '301']['route_id'].tolist()  # ION is route 301
    bus_routes = routes[routes['route_type'] == 3]['route_id'].tolist()
    
    print(f"🚊 Found {len(ion_routes)} ION routes")
    print(f"🚌 Found {len(bus_routes)} bus routes")
    
    # Get trips for each mode
    ion_trips = trips[trips['route_id'].isin(ion_routes)]
    bus_trips = trips[trips['route_id'].isin(bus_routes)]
    
    # Get stop IDs for each mode
    ion_stop_ids = stop_times[stop_times['trip_id'].isin(ion_trips['trip_id'])]['stop_id'].unique()
    bus_stop_ids = stop_times[stop_times['trip_id'].isin(bus_trips['trip_id'])]['stop_id'].unique()
    
    print(f"📍 Found {len(ion_stop_ids)} ION stops")
    print(f"📍 Found {len(bus_stop_ids)} bus stops")
    
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
    
    # Filter timetables for specified time period
    print(f"⏰ Filtering timetables for {args.time[0]} - {args.time[1]}...")
    st_with_routes = stop_times.merge(trips[['trip_id', 'route_id']], on='trip_id')
    
    # Filter for valid times in the window
    mask = (st_with_routes['arrival_time'].notna() & 
            st_with_routes['arrival_time'].str.match(r'^\d{2}:\d{2}:\d{2}$', na=False) &
            (st_with_routes['arrival_time'] >= args.time[0]) & 
            (st_with_routes['arrival_time'] <= args.time[1]))
    ion_times = st_with_routes[st_with_routes['route_id'].isin(ion_routes) & mask].copy()
    
    mask = (st_with_routes['departure_time'].notna() & 
            st_with_routes['departure_time'].str.match(r'^\d{2}:\d{2}:\d{2}$', na=False) &
            (st_with_routes['departure_time'] >= args.time[0]) & 
            (st_with_routes['departure_time'] <= args.time[1]))
    bus_times = st_with_routes[st_with_routes['route_id'].isin(bus_routes) & mask].copy()
    
    print(f"✓ Found {len(ion_times)} ION arrivals")
    print(f"✓ Found {len(bus_times)} bus departures")
    
    # Spatial join to find nearby bus stops
    print(f"🔍 Finding bus stops within {args.buffer}m of ION stations...")
    ion_buffers = ion_stops.copy()
    ion_buffers['geometry'] = ion_buffers.buffer(args.buffer)
    
    nearby = gpd.sjoin(bus_stops, ion_buffers, predicate='within', how='inner')
    print(f"✓ Found {len(nearby)} nearby stop pairs")
    
    # Calculate transfers
    print("🔄 Calculating transfer opportunities...")
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
                
                if 0 <= diff_minutes <= args.transfer_time:
                    transfers.append({
                        'ion_stop_id': ion_stop_id,
                        'bus_route_id': b_row['route_id']
                    })
    
    # Aggregate results
    if transfers:
        transfers_df = pd.DataFrame(transfers)
        xfer_counts = transfers_df.groupby('ion_stop_id')['bus_route_id'].nunique().reset_index()
        xfer_counts.columns = ['stop_id', 'bus_xfer_routes']
        print(f"✓ Found {len(transfers)} total transfer opportunities")
    else:
        xfer_counts = pd.DataFrame(columns=['stop_id', 'bus_xfer_routes'])
        print("⚠️  No transfer opportunities found in this time window")
    
    # Prepare output
    ion_stops_wgs = ion_stops.to_crs(epsg=4326)
    output = ion_stops_wgs.merge(xfer_counts, on='stop_id', how='left')
    output['bus_xfer_routes'] = output['bus_xfer_routes'].fillna(0).astype(int)
    
    # Save outputs
    print("\n💾 Saving results...")
    csv_cols = ['stop_id', 'stop_name', 'bus_xfer_routes']
    csv_path = args.output_dir / "ion_transfer_index.csv"
    output[csv_cols].to_csv(csv_path, index=False)
    print(f"✓ CSV saved to {csv_path}")
    
    geojson_path = args.output_dir / "ion_transfer_index.geojson"
    output.to_file(geojson_path, driver='GeoJSON')
    print(f"✓ GeoJSON saved to {geojson_path}")
    
    map_path = args.output_dir / "ion_transfer_map.html"
    create_map(output, map_path, args.buffer)
    
    # Save summary statistics
    summary = {
        'analysis_date': dt.datetime.now().isoformat(),
        'service_date': args.date,
        'time_window': f"{args.time[0]} - {args.time[1]}",
        'buffer_metres': args.buffer,
        'max_transfer_minutes': args.transfer_time,
        'total_ion_stops': len(output),
        'stops_with_transfers': int((output['bus_xfer_routes'] > 0).sum()),
        'max_routes_at_stop': int(output['bus_xfer_routes'].max()),
        'total_transfer_opportunities': len(transfers) if transfers else 0,
        'top_stations': output.nlargest(5, 'bus_xfer_routes')[['stop_name', 'bus_xfer_routes']].to_dict('records')
    }
    
    summary_path = args.output_dir / "analysis_summary.json"
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"✓ Summary saved to {summary_path}")
    
    # Print summary
    print("\n📊 Analysis Summary")
    print("=" * 50)
    print(f"Total ION stops analyzed: {summary['total_ion_stops']}")
    print(f"Stops with transfer opportunities: {summary['stops_with_transfers']}")
    print(f"Maximum routes at a single stop: {summary['max_routes_at_stop']}")
    print("\nTop 5 stations by transfer options:")
    for station in summary['top_stations']:
        print(f"  • {station['stop_name']}: {station['bus_xfer_routes']} routes")
    print("=" * 50)
    print(f"\n✅ Analysis complete! View the interactive map at:")
    print(f"   {map_path.absolute()}")


if __name__ == "__main__":
    main()