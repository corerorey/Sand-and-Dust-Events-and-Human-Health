import pandas as pd
import numpy as np

try:
    from tabpfn import TabPFNClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, classification_report
except ImportError:
    print("TabPFN and sklearn are required for this module.")
    TabPFNClassifier = None

def run_tabpfn_baseline(df, features, target_col):
    """
    Train and evaluate a fast TabPFN classifier to predict High Risk vs Low Risk days.
    
    TabPFN shines on tabular data (<1000 samples, <100 features) and requires 
    minimal hyperparameter tuning to act as a robust baseline indicator.
    """
    if TabPFNClassifier is None:
        raise ImportError("Please install tabpfn: pip install tabpfn")
        
    df = df.dropna(subset=features + [target_col])
    
    X = df[features]
    y = df[target_col]
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Initialize TabPFN (N=ensembling runs)
    classifier = TabPFNClassifier(device='cpu', N_ensemble_configurations=4)
    
    classifier.fit(X_train, y_train)
    
    y_pred, p_eval = classifier.predict(X_test, return_winning_probability=True)
    
    print("TabPFN Performance:")
    print(classification_report(y_test, y_pred))
    
    return classifier, y_pred, X_test, y_test

if __name__ == "__main__":
    print("Baseline ML Risk Classifier (TabPFN) module initialized.")
