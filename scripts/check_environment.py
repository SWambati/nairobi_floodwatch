from config.paths import (
    DATA_ROOT,
    FLOOD_LABELS_DIR,
    WEATHER_DIR,
    SPATIAL_DIR,
    ENVIRONMENTAL_DIR,
    OUTPUT_ROOT
)


paths = {
    "Data root": DATA_ROOT,
    "Flood labels": FLOOD_LABELS_DIR,
    "Weather": WEATHER_DIR,
    "Spatial": SPATIAL_DIR,
    "Environmental": ENVIRONMENTAL_DIR,
    "Outputs": OUTPUT_ROOT,
}


print("Nairobi FloodWatch Environment Check")
print("-" * 40)

for name, path in paths.items():
    status = "OK" if path.exists() else "MISSING"

    print(
        f"{name:<20} "
        f"{status:<8} "
        f"{path}"
    )