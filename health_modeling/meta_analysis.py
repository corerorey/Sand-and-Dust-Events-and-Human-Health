import pandas as pd
import numpy as np

def run_meta_analysis(site_results):
    """
    Run a random-effects meta analysis across city-specific log(RR) estimations.
    
    Inputs:
    site_results is a list of dictionaries, one per site:
      {"site_id": ..., "log_rr": x, "se": y}
    """
    df = pd.DataFrame(site_results)
    
    if len(df) < 2:
        return df
        
    # Calculate weights based on standard error
    df['variance'] = df['se'] ** 2
    df['weight'] = 1 / df['variance']
    
    # Q-statistic for heterogeneity
    w_sum = df['weight'].sum()
    weighted_mean_effect = (df['weight'] * df['log_rr']).sum() / w_sum
    df['Q_i'] = df['weight'] * ((df['log_rr'] - weighted_mean_effect) ** 2)
    Q = df['Q_i'].sum()
    
    k = len(df)
    C = w_sum - ( (df['weight']**2).sum() / w_sum )
    
    tau_squared = max(0, (Q - (k - 1)) / C) if C > 0 else 0
    
    # Random effects weights
    df['re_variance'] = df['variance'] + tau_squared
    df['re_weight'] = 1 / df['re_variance']
    
    re_w_sum = df['re_weight'].sum()
    pooled_effect = (df['re_weight'] * df['log_rr']).sum() / re_w_sum
    pooled_variance = 1 / re_w_sum
    pooled_se = np.sqrt(pooled_variance)
    
    # Return aggregated info
    agg_result = {
        "pooled_log_rr": pooled_effect,
        "pooled_irr": np.exp(pooled_effect),
        "pooled_se": pooled_se,
        "lower_95_ci": np.exp(pooled_effect - 1.96 * pooled_se),
        "upper_95_ci": np.exp(pooled_effect + 1.96 * pooled_se),
        "tau_squared": tau_squared,
        "heterogeneity_I2": max(0, 100 * (Q - (k-1))/Q) if Q > 0 else 0
    }
    
    return agg_result

if __name__ == "__main__":
    print("Multi-site meta-analysis module initialized.")
