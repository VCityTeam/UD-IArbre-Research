import pandas as pd
import geopandas as gpd
import numpy as np
import matplotlib.pyplot as plt

# https://www.data.gouv.fr/fr/datasets/emplacement-et-etat-des-capteurs-de-temperature-en-temps-reel-dans-la-metropole-de-lyon/
# https://www.data.gouv.fr/fr/datasets/temperatures-en-temps-reel-sur-la-metropole-de-lyon/#/resources


# Load CSV file
file_path = "../temperatures_lyon_capteurs.csv"  # Update with the actual file path
df = pd.read_csv(file_path, sep=";", decimal=",")  # Handle European decimal separator

# Convert horodate to datetime
df["horodate"] = pd.to_datetime(df["horodate"], format="%Y-%m-%d %H:%M:%S%z")

# Sort values by time
df = df.sort_values(by=["deveui", "horodate"])

start_date = pd.to_datetime("2024-07-20").tz_localize("Europe/Paris")  # Adjust based on your timezone
end_date = pd.to_datetime("2024-07-22").tz_localize("Europe/Paris")
df_to_plot = df[(df["horodate"] >= start_date) & (df["horodate"] <= end_date)]

# Plot temperature evolution for each deveui and their average on the same figure
plt.figure(figsize=(12, 6))

# Define a color map for consistent coloring
colors = plt.cm.tab10(np.linspace(0, 1, len(df_to_plot["deveui"].unique())))

for (deveui, group), color in zip(df_to_plot.groupby("deveui"), colors):
    plt.plot(group["horodate"], group["degre_celsius"], label=f"{deveui} (Temp)", color=color)

# Calculate average temperature per device
average_temperatures = df_to_plot.groupby("deveui")["degre_celsius"].mean()

# Plot average temperatures as horizontal lines
for deveui, avg_temp, color in zip(average_temperatures.index, average_temperatures, colors):
    plt.axhline(y=avg_temp, color=color, linestyle="--", label=f"{deveui} (Avg)")

plt.xlabel("Time")
plt.ylabel("Temperature (°C)")
plt.title("Temperature Evolution and Average per Device")
plt.legend(title="deveui", bbox_to_anchor=(1.05, 1), loc="upper left")
plt.xticks(rotation=45)
plt.grid()

plt.show()



#Zonal statistics --> stats de raster sur polygones de vecteurs
