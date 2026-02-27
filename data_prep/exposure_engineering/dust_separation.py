import pandas as pd
import numpy as np

def calculate_coarse_fraction_proxy(df, pm10_col='pm10', pm25_col='pm25'):
    """
    Method 1: Coarse Fraction Proxy (PM10 - PM2.5).
    Elevated coarse fraction is a strong indicator of dust during an event 
    compared to background anthropogenic pollution (which is usually fine PM).
    """
    df['pm_coarse'] = df[pm10_col] - df[pm25_col]
    df['coarse_ratio'] = df['pm_coarse'] / df[pm10_col]
    
    # Simple heuristic: If coarse ratio > 0.6 and total PM10 > 150, flag as Dust dominance
    df['is_dust_dominated_proxy'] = ((df['coarse_ratio'] > 0.6) & (df[pm10_col] > 150)).astype(int)
    return df

def align_cams_dust_tracer(df, cams_df, date_col='date'):
    """
    Method 2: CAMS / MERRA-2 Dust Tracer Alignment.
    Use reanalysis Dust Surface Concentration (DUSMASS) as the explicit 
    fraction of total PM10 to separate natural dust from other sources.
    
    cams_df must have ['date', 'cams_dust_pm10', 'cams_total_pm10']
    """
    merged = pd.merge(df, cams_df, on=date_col, how='left')
    
    merged['cams_dust_fraction'] = merged['cams_dust_pm10'] / merged['cams_total_pm10']
    merged['cams_dust_fraction'] = merged['cams_dust_fraction'].fillna(0)
    
    # We can then apply this fraction to the Ground-truth PM10
    # to estimate Ground-truth Dust PM10
    if 'pm10' in merged.columns:
        merged['ground_dust_pm10_est'] = merged['pm10'] * merged['cams_dust_fraction']
        merged['ground_nondust_pm10_est'] = merged['pm10'] - merged['ground_dust_pm10_est']
        
    return merged

if __name__ == "__main__":
    print("Methods for separating Dust vs Non-Dust PM are implemented.")
