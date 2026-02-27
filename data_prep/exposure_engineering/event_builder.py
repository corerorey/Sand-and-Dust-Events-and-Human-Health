import os
import pandas as pd
import numpy as np

try:
    from netCDF4 import Dataset
    import netCDF4
except ImportError:
    print("netCDF4 package is required.")
    Dataset = None

class EventObject:
    def __init__(self, event_id, start_time, end_time, duration):
        self.event_id = event_id
        self.start_time = start_time
        self.end_time = end_time
        self.duration_hours = duration
        
        self.footprint_polygons = [] # To be implemented via Satellite/Map data
        self.intensity_metrics = {}
        self.detection_method = "Unknown"
        self.health_aligned_data = None
        
    def add_intensity_metric(self, name, timeseries):
        self.intensity_metrics[name] = timeseries
        
    def set_detection(self, method, config):
        self.detection_method = method
        self.detection_config = config
        
    def align_health_data(self, df_health):
        """Align health data to this event window (with Optional lags)"""
        # A placeholder for Phase 2 DLNM/GAM inputs
        pass

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration_hours,
            "metrics": list(self.intensity_metrics.keys())
        }

def build_event_catalogs(merra_events_csv, cnemc_nc_path, output_dir):
    """
    Reads MERRA-2 events and extracts concurrent CNEMC spatial PM10 data,
    packaging them into EventObjects.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Load Events
    df_events = pd.read_csv(merra_events_csv)
    df_events['start_local'] = pd.to_datetime(df_events['start_local'])
    df_events['end_local'] = pd.to_datetime(df_events['end_local'])
    
    events = []
    
    # 2. Open CNEMC NC
    try:
        nc = Dataset(cnemc_nc_path, 'r')
        nc_times = netCDF4.num2date(nc.variables['time'][:], nc.variables['time'].units, only_use_cftime_datetimes=False)
        nc_times = pd.to_datetime([t.strftime('%Y-%m-%d %H:%M:%S') for t in nc_times])
        
        sites = nc.variables['site_number'][:]
        lats = nc.variables['lat'][:]
        lons = nc.variables['lon'][:]
        
        # We need PM10 for dust proxies
        pm10_var = None
        for v in nc.variables:
            if 'PM10' in v.upper():
                pm10_var = v
                break
                
        if not pm10_var:
            print("No PM10 variable found in CNEMC NetCDF.")
            return

        pm10_data = nc.variables[pm10_var]
        
    except Exception as e:
        print(f"Skipping CNEMC integration due to error: {e}")
        nc = None

    for _, row in df_events.iterrows():
        evt = EventObject(
            event_id=row['event_id'],
            start_time=row['start_local'],
            end_time=row['end_local'],
            duration=row['duration_hours']
        )
        evt.set_detection("MERRA-2 Dual Criteria", {"primary": row['primary_var'], "secondary": row.get('secondary_var')})
        
        # Extract CNEMC spatial distribution during this event
        if nc is not None:
            mask = (nc_times >= row['start_local']) & (nc_times <= row['end_local'])
            valid_idx = np.where(mask)[0]
            if len(valid_idx) > 0:
                # Slice time x space
                event_pm10 = pm10_data[valid_idx, :]
                mean_pm10_spatial = np.nanmean(event_pm10, axis=0)
                
                # Store as DataFrame
                df_spatial = pd.DataFrame({
                    "site_id": sites,
                    "lat": lats,
                    "lon": lons,
                    "event_mean_pm10": mean_pm10_spatial
                })
                # Drop all-NaN sites
                df_spatial = df_spatial.dropna(subset=['event_mean_pm10'])
                
                evt.add_intensity_metric("cnemc_spatial_pm10", df_spatial)
                
                # Save spatial footprint for this event
                out_csv = os.path.join(output_dir, f"event_{row['event_id']}_cnemc_footprint.csv")
                df_spatial.to_csv(out_csv, index=False)
                
        events.append(evt)
        print(f"Processed Event {evt.event_id}: {evt.duration_hours}h")

    if nc:
        nc.close()
        
    print(f"Successfully built {len(events)} EventObjects.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build Event Objects and align MERRA-2 with CNEMC.")
    parser.add_argument("--merra_csv", type=str, required=True, help="Path to dust_events_summary.csv")
    parser.add_argument("--cnemc_nc", type=str, required=True, help="Path to CNEMC NetCDF file (.nc)")
    parser.add_argument("--out_dir", type=str, default="./out_event_objects", help="Output directory for mapped events")
    
    args = parser.parse_args()
    build_event_catalogs(args.merra_csv, args.cnemc_nc, args.out_dir)

