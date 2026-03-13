# HYSPLIT Trajectory Analysis (Hysplit 轨迹与源-汇分析)

This directory is an analytical placeholder for the HYSPLIT (Hybrid Single-Particle Lagrangian Integrated Trajectory) model outputs.

## Workflow

1. **Trajectory Generation:** 
   Backward trajectories are simulated using the offline NOAA HYSPLIT PC version. The meteorological inputs (e.g., GDAS, NCEP/NCAR Reanalysis) are driven via the GUI.
2. **Analysis Output:**
   Trajectories ending at Lanzhou (and other relevant receptors) during the event windows are computed. 
3. **Data Linkage:**
   The output text files from HYSPLIT are saved here and parsed manually or programmatically to derive transmission pathways and source-receptor relationships. These metrics act as meteorological modifiers alongside our other features in the exposure-health models.
