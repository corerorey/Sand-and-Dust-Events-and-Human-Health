# DLNM Implementation Boilerplate using R via rpy2 or standalone
# Distributed Lag Non-linear Models are best implemented in R's `dlnm` package.

# Check and load required packages
required_packages <- c("dlnm", "splines", "stats")
for (pkg in required_packages) {
  if (!require(pkg, character.only = TRUE)) {
    message(paste("Package", pkg, "not found. Attempting to install..."))
    install.packages(pkg, repos = "https://cloud.r-project.org")
    if (!require(pkg, character.only = TRUE)) {
      stop(paste("Failed to install and load package:", pkg))
    }
  }
}


fit_dlnm <- function(df, outcome_var='measles_cases', exposure_var='pm10_daily_mean', max_lag = 14) {
  
  # 1. Define the cross-basis for the exposure
  # Using a natural cubic spline for the exposure-response and for the lag-response
  cb_exposure <- crossbasis(
    df[[exposure_var]], 
    lag = max_lag, 
    argvar = list(fun = "ns", df = 3),
    arglag = list(fun = "ns", df = 4)
  )
  
  # 2. Define confounders
  # Assumes df has 'time', 'temp', 'rh', 'dow'
  cb_temp <- crossbasis(df$temp, lag=max_lag, argvar=list(fun="ns", df=4), arglag=list(fun="ns", df=4))
  
  # 3. Fit the model (Quasi-Poisson)
  formula <- as.formula(paste(outcome_var, "~ cb_exposure + cb_temp + ns(time, df=7*length(unique(format(df$date, '%Y')))) + as.factor(dow)"))
  
  model <- glm(formula, family = quasipoisson(), data = df)
  
  # 4. Predict the effects
  pred_exposure <- crosspred(cb_exposure, model, by=10)
  
  # Return the prediction object which contains the IRRs and CIs
  return(list(model=model, crossbasis=cb_exposure, predictions=pred_exposure))
}

# Typical use sequence:
# 1. Output the daily `measles_cases` and `pm10_daily_mean` via Python `health_data_loader.py` to CSV.
# 2. Run this DLNM script (max_lag=14 as standard) in R.
# 3. Extract the computed 3D surface matrix and relative risks (RRs) back to Python for plotting.
