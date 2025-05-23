#!/usr/bin/env python3
"""Generate transfer maps for multiple buffer distances"""

import subprocess
import sys
from pathlib import Path

# Generate maps for different distances
distances = [50, 100, 150, 200, 250, 300, 350, 400, 450, 500]

print("Generating maps for different transfer distances...")

# Read the original script
script_path = Path("build_transfer_index.py")
original_content = script_path.read_text()

for dist in distances:
    print(f"\nGenerating map for {dist}m buffer...")
    
    # Modify the BUFFER_METRES parameter
    modified_content = original_content.replace(
        "BUFFER_METRES = 100", 
        f"BUFFER_METRES = {dist}"
    )
    
    # Modify output paths to include distance
    modified_content = modified_content.replace(
        'OUT_DIR / "ion_transfer_index.csv"',
        f'OUT_DIR / "ion_transfer_index_{dist}m.csv"'
    )
    modified_content = modified_content.replace(
        'OUT_DIR / "ion_transfer_index.geojson"',
        f'OUT_DIR / "ion_transfer_index_{dist}m.geojson"'
    )
    modified_content = modified_content.replace(
        'OUT_DIR / "ion_transfer_map.html"',
        f'OUT_DIR / "ion_transfer_map_{dist}m.html"'
    )
    
    # Write temporary script
    temp_script = Path(f"temp_build_{dist}.py")
    temp_script.write_text(modified_content)
    
    # Run it
    result = subprocess.run([sys.executable, str(temp_script)], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Error generating map for {dist}m:")
        print(result.stderr)
    else:
        print(f"✓ Generated ion_transfer_map_{dist}m.html")
    
    # Clean up temp script
    temp_script.unlink()

print("\nCreating index page...")

# Create an index HTML page with links to all maps
index_html = """<!DOCTYPE html>
<html>
<head>
    <title>ION Transfer Maps - Multiple Distances</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
        }
        h1 { color: #333; }
        .controls {
            background: #f0f0f0;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
        }
        .slider-container {
            margin: 20px 0;
        }
        input[type="range"] {
            width: 100%;
            margin: 10px 0;
        }
        .value-display {
            font-size: 24px;
            font-weight: bold;
            text-align: center;
            color: #0066cc;
        }
        iframe {
            width: 100%;
            height: 600px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }
        .info {
            background: #e8f4f8;
            padding: 15px;
            border-radius: 4px;
            margin: 20px 0;
        }
    </style>
</head>
<body>
    <h1>ION Light Rail - Bus Transfer Analysis</h1>
    
    <div class="info">
        <p><strong>What this shows:</strong> Bus transfer opportunities at ION stations during morning peak hours (7:00-9:00 AM)</p>
        <p><strong>How to use:</strong> Adjust the slider to change the walking distance from ION stations to bus stops</p>
    </div>
    
    <div class="controls">
        <h2>Transfer Walking Distance</h2>
        <div class="slider-container">
            <input type="range" id="distanceSlider" min="50" max="500" value="100" step="50">
            <div class="value-display"><span id="distanceValue">100</span> meters</div>
        </div>
    </div>
    
    <iframe id="mapFrame" src="ion_transfer_map_100m.html"></iframe>
    
    <script>
        const slider = document.getElementById('distanceSlider');
        const valueDisplay = document.getElementById('distanceValue');
        const mapFrame = document.getElementById('mapFrame');
        
        slider.addEventListener('input', function() {
            const distance = this.value;
            valueDisplay.textContent = distance;
            mapFrame.src = `ion_transfer_map_${distance}m.html`;
        });
    </script>
</body>
</html>"""

index_path = Path("output/index.html")
index_path.write_text(index_html)

print(f"\n✓ Created index.html")
print(f"\nOpen output/index.html in your browser to use the interactive distance slider!")