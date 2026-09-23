# Shadow Modeling 
This program generates cast-shadow maps for the **Lyon metropolitan area** in **two different ways** : <br> <br>
**Method 1** <br>
Generates a cast-shadow map in which shadows cast by vegetation are calculated in the same way as shadows cast by other urban elements.
<br> <br>
**Method 2** <br>
Generates a cast-shadow map using a separate vegetation-shadow calculation, taking into account **only the foliage** when computing shadows cast by vegetation.

<br>

<p align= "center"><img width="712" height="484" alt="image" src="https://github.com/user-attachments/assets/0a916c3a-b735-4b7f-93f8-b8513784a949" /></p>


<br>

## Installation
The following sections detail the configuration steps for launching the program.

### Prerequisites
- [Install Docker](https://www.docker.com/get-started/).
- Install WSL 2 :
```
wsl --install --no-distribution
```
- restart your machine.
- Clone this repository.

<br>

### Build Images and Run Containers
Open a terminal at the root of the project, then run the following commands.

#### Build the Docker image
```
docker build --no-cache -t shadow_modeling .
```

#### Generate the shadow map using method 1:
```
docker run -it --rm -v "${PWD}:/app" shadow_modeling Method1.py
```

#### Generate the shadow map using method 2:
```
docker run -it --rm -v "${PWD}:/app" shadow_modeling Method2.py
```


### Input data

The program takes as input several pieces of information provided by the user:
- A date in numerical format **(e.g., 07/09/2023)**.
- The X_min and Y_min positions of the desired LiDAR tile **(e.g., X_min : 1848000 ; Y_min : 5180000)**.
The tile positions are available on the website of **[DataGrandLyon (LiDAR 2023)](https://data.grandlyon.com/portail/fr/jeux-de-donnees/nuage-de-points-lidar-2023-de-la-metropole-de-lyon/donnees)**.
- The program for both methods can be launched for a single projection or for a period of time: <br> `Do you want a single shadow projection? (y/n)`
  - If you have chosen `y` the program will execute the code to project shadows at a **single phase** of the day.
  - If you chose `n` the program will ask you for the **start** and **end** time of the **time period** and generate the shadow map for that period.
- After calculating the sun's positions for the desired date, the program asks the user to select a time of day to use for the calculation **(e.g., Program "2: 10H20" -> User : "2")**.

<br>

### Result

The generated shadow map, depending on the chosen method **Method 1** or **2**, is saved in **TIFF** format in the **`Data`** folder located at the project root.
In the case of calculating shadows over a period of time,The shadows are darker or lighter depending on whether the area remained shaded for a long time during the selected period!

**To properly view TIFF files, you must open them in a suitable viewer.** <br>
You can use [VScode](https://code.visualstudio.com) by installing the **GeoTIFF Viewer** extension, and clicking on **`Open with : GeoTIFF Viewer`**. <br> In order to better visualize shadows it is recommended to use the **Colormap :** `Grayscale`.

<p align= "center"><img width="546" height="546" alt="method2_period" src="https://github.com/user-attachments/assets/c6e4a60d-19dd-44a1-81fb-b6245af9c3bf" /></p>

## Team
- Karima Ouadah
- John Samuel
- Gilles Gesquière


