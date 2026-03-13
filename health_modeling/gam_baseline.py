import pandas as pd
import numpy as np
try:
    import statsmodels.api as sm
    from pygam import PoissonGAM, s, l, f
except ImportError:
    print("statsmodels and pygam are required for GAM modeling.")

from health_data_loader import load_aligned_dataset

def prep_gam_dataset():
    """
    Load the merged daily health occurrences (e.g. Measles cases) with event metrics/meteorology.
    Assumes 0-14 days lag is handled either via shifted columns or externally.
    """
    df = load_aligned_dataset()
    if df is None:
        return None
    
    # Fill event intensity with 0 where no event is detected
    if 'pm10_daily_mean' in df.columns:
        df['pm10_daily_mean'] = df['pm10_daily_mean'].fillna(0)
        df['is_event_day'] = (df['pm10_daily_mean'] > 0).astype(int)
    
    # DOW and Holiday controls
    df['day_of_week'] = pd.to_datetime(df['date']).dt.dayofweek
    df['holiday'] = 0 # To be mapped externally
    
    # Time index for long-term trend
    df['time'] = np.arange(len(df))
    
    # Expect measles_cases as the primary outcome
    return df.dropna(subset=['measles_cases', 'temp_mean', 'rh_mean'])

def fit_baseline_gam(df_gam):
    """
    Fit a baseline Poisson GAM:
    log(E[Measles_t]) = alpha + beta * PM10_event_t + s(Time) + s(Temp) + s(RH) + DOW + Holiday
    """
    if 'measles_cases' not in df_gam.columns:
        raise ValueError("Missing 'measles_cases' in DataFrame")
        
    X = df_gam[['time', 'temp_mean', 'rh_mean', 'pm10_daily_mean', 'day_of_week', 'holiday']]
    y = df_gam['measles_cases']

    # Using pyGAM
    # We apply: spline on time (seasonality/trend), spline on temp/RH, linear on event intensity, factor on categorical DOW/Holiday
    gam = PoissonGAM(s(0, n_splines=10) + s(1) + s(2) + l(3) + f(4) + f(5)).fit(X, y)
    
    return gam

def extract_gam_results(gam, df_gam):
    """
    Summarize the relative risk (RR) and confidence intervals.
    """
    summary = gam.summary()
    
    # In a typical GAM, we compute RR of the continuous term using exp(beta * unit_increase)
    # Pygam requires custom partial dependence for inference on terms
    
    return summary

if __name__ == "__main__":
    print("--- Phase 2: GAM Baseline Modeling ---")
    print("GAM boilerplate script ready. Will integrate with Lanzhou daily Measles datasets seamlessly.")
