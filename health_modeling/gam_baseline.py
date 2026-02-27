import pandas as pd
import numpy as np
try:
    import statsmodels.api as sm
    from pygam import PoissonGAM, s, l, f
except ImportError:
    print("statsmodels and pygam are required for GAM modeling.")

def prep_gam_dataset(health_counts, event_object_metrics, weather_df):
    """
    Merge the daily health occurrences with event metrics and meteorological controls.
    """
    df = pd.merge(health_counts, event_object_metrics, on='date', how='left')
    df = pd.merge(df, weather_df, on='date', how='left')
    
    # Fill event intensity with 0 where no event is detected
    df['event_intensity_pm10'] = df['event_intensity_pm10'].fillna(0)
    df['is_event_day'] = (df['event_intensity_pm10'] > 0).astype(int)
    
    # DOW and Holiday controls
    df['day_of_week'] = pd.to_datetime(df['date']).dt.dayofweek
    df['holiday'] = 0 # To be mapped externally
    
    # Time index for long-term trend
    df['time'] = np.arange(len(df))
    
    return df.dropna(subset=['outcome_count', 'temp_mean', 'rh_mean'])

def fit_baseline_gam(df_gam):
    """
    Fit a baseline Poisson GAM:
    log(E[Y_t]) = alpha + beta * Event_t + s(Time) + s(Temp) + s(RH) + DOW + Holiday
    """
    if 'outcome_count' not in df_gam.columns:
        raise ValueError("Missing 'outcome_count' in DataFrame")
        
    X = df_gam[['time', 'temp_mean', 'rh_mean', 'event_intensity_pm10', 'day_of_week', 'holiday']]
    y = df_gam['outcome_count']

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
    print("GAM boilerplate script ready. Will integrate with EventObjects seamlessly.")
