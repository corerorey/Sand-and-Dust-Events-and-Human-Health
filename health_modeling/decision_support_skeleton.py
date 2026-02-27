import pandas as pd
import numpy as np

def prepare_rehearsal_learning_context(df_health, df_exposure):
    """
    Preprocess data into the specific (X, Z, Y) tuples required by Grad-RH / AUF-MICNS.
    
    X (Context): Temperature, Month, Population Density, Baseline Dust Forecast.
    Z (Intervention/Action Space): Binary action (1 if public warning issued, 0 if not), Resource allocations.
    Y (Undesired Outcome): Excess mortality / Admission surge.
    """
    
    # A skeleton mapper for testing framework inputs
    
    # Ensure aligned
    df = pd.merge(df_health, df_exposure, on=['date', 'site_id'], how='inner')
    
    # 1. Context (X)
    X_cols = ['temp_mean', 'rh_mean', 'baseline_dust_intensity', 'day_of_week']
    X = df[X_cols].to_numpy()
    
    # 2. Intervention Action Space (Z)
    # Historically, we might infer this if we have "public warning" records
    if 'public_action_taken' in df.columns:
        Z = df['public_action_taken'].to_numpy()
    else:
        # Placeholder
        Z = np.zeros(len(df))
        
    # 3. Outcome (Y)
    Y = df['excess_health_count'].to_numpy()
    
    print(f"Prepared Rehearsal Context: X shape: {X.shape}, Z shape: {Z.shape}, Y shape: {Y.shape}")
    
    return {"X": X, "Z": Z, "Y": Y}
    
if __name__ == "__main__":
    print("Decision Support (Rehearsal Learning) skeleton mapper initialized.")
