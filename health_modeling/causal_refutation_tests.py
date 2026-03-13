import os
try:
    import dowhy
    from dowhy import CausalModel
except ImportError:
    print("dowhy package required for causal frameworks.")
    CausalModel = None

# Using the centralized data loader
from health_data_loader import load_aligned_dataset

def build_dust_measles_dag():
    """
    Returns a string representation of the assumed Directed Acyclic Graph (DAG) 
    for Dust/Meteorological Synergy -> Measles Incidence.
    
    Pathways defined by the proposal:
    Month -> Temp, Humidity -> Dust, Measles (Seasonality)
    Temp, Humidity -> Dust (Meteorological enablers of dust events)
    Vaccination, School Calendar -> Measles (Infectious disease specifics)
    Dust (Synergy Threshold) -> Measles (Direct physical/biological mechanism)
    """
    causal_graph = """
    digraph {
    Month;
    Temperature;
    Humidity;
    Vaccination_Proxy;
    School_Calendar;
    Synergy_Threshold_Event;
    Measles_Cases;

    Month -> Temperature;
    Month -> Humidity;
    Month -> Synergy_Threshold_Event;
    Month -> Vaccination_Proxy;
    Month -> School_Calendar;

    Temperature -> Synergy_Threshold_Event;
    Humidity -> Synergy_Threshold_Event;
    
    Month -> Measles_Cases;
    Temperature -> Measles_Cases;
    Humidity -> Measles_Cases;
    Vaccination_Proxy -> Measles_Cases;
    School_Calendar -> Measles_Cases;
    
    Synergy_Threshold_Event -> Measles_Cases;
    }
    """
    return causal_graph

def run_causal_refutations(df, treatment='Synergy_Threshold_Event', outcome='Measles_Cases'):
    """
    Executes DoWhy causal identification, OrthoForest estimation, and Refutation Tests (RCC/PT).
    """
    if CausalModel is None:
        return None
        
    out_file = f"causal_results_{treatment}_{outcome}.txt"
    # Ensure starting with an empty log
    open(out_file, "w+").close()

    dowhy.causal_refuter.CausalRefuter.DEFAULT_NUM_SIMULATIONS = 100

    model = CausalModel(
        data=df,
        treatment=treatment,
        outcome=outcome,
        graph=build_dust_measles_dag().replace("\n", " ")
    )

    with open(out_file, "a") as f:
        print("####### Model #######", file=f)
        print("Common Causes:", model._common_causes, file=f)
        print("Effect Modifiers:", model._effect_modifiers, file=f)
        print("Outcome:", model._outcome, file=f)
        print("Treatment:", model._treatment, file=f)

    # 1. Identification
    estimand = model.identify_effect(proceed_when_unidentifiable=True)

    with open(out_file, "a") as f:
        print("\n####### Identified Estimand #######", file=f)
        print(estimand, file=f)

    # 2. Estimation 
    # Linear 
    estimate_li = model.estimate_effect(estimand, method_name="backdoor.linear_regression")
    
    # EconML DML OrthoForest
    try:
        # Note: EconML package required. 
        estimate_forest = model.estimate_effect(
            estimand,
            method_name="backdoor.econml.ortho_forest.DiscreteTreatmentOrthoForest",
            method_params={"init_params": {"n_jobs": -1}, "fit_params": {}}
        )
    except Exception as e:
        estimate_forest = None
        print(f"OrthoForest estimation failed (possibly missing econml): {e}")

    with open(out_file, "a") as f:
        print("\n####### Linear Estimate #######", file=f)
        print(estimate_li, file=f)
        if estimate_forest:
            print("\n####### Forest Estimate #######", file=f)
            print(estimate_forest, file=f)

    # 3. Refutations
    # Random Common Cause (RCC)
    refutation_li_rcc = model.refute_estimate(estimand, estimate_li, method_name='random_common_cause')
    # Placebo Treatment (PT)
    refutation_li_ptr = model.refute_estimate(estimand, estimate_li, method_name='placebo_treatment_refuter')

    with open(out_file, "a") as f:
        print("\n####### Linear RCC Refutation #######", file=f)
        print(refutation_li_rcc, file=f)
        
        print("\n####### Linear PTR Refutation #######", file=f)
        print(refutation_li_ptr, file=f)
        
        if estimate_forest:
            refutation_forest_rcc = model.refute_estimate(estimand, estimate_forest, method_name='random_common_cause')
            refutation_forest_ptr = model.refute_estimate(estimand, estimate_forest, method_name='placebo_treatment_refuter')
            
            print("\n####### Forest RCC Refutation #######", file=f)
            print(refutation_forest_rcc, file=f)
            
            print("\n####### Forest PTR Refutation #######", file=f)
            print(refutation_forest_ptr, file=f)

    print(f"Causal analysis complete. Results dumped to {out_file}")

if __name__ == "__main__":
    print("--- Phase 3: DoWhy Causal Inference & Refutation Tests ---")
    data = load_aligned_dataset()
    
    if data is not None:
        # Create dummy columns to satisfy the DAG if they don't exist yet
        required_cols = ['Month', 'Temperature', 'Humidity', 'Vaccination_Proxy', 
                         'School_Calendar', 'Synergy_Threshold_Event', 'Measles_Cases']
        for col in required_cols:
            if col not in data.columns:
                data[col] = 0 # Placeholder injection
                
        run_causal_refutations(data)
    else:
        print("Dataset not ready. Causal Refutation pipeline terminating safely.")
