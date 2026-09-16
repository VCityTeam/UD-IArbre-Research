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

<br>

### Result

The generated shadow map, depending on the chosen method **Method 1 or 2**, is saved in **TIFF** format in the **`Data`** folder located at the project root.

<br>

## Input data

The program takes as input several pieces of information provided by the user:
- A date in numerical format **(e.g., 07/09/2023)**.
- The X_min and Y_min positions of the desired LiDAR tile **(e.g., X_min : 1848000 ; Y_min : 5180000)**.
The tile positions are available on the website of **[DataGrandLyon](https://data.grandlyon.com/portail/fr/jeux-de-donnees/nuage-de-points-lidar-2023-de-la-metropole-de-lyon/donnees)**.
- After calculating the sun's positions for the desired date, the program asks the user to select a time of day to use for the calculation **(e.g., Programme "2: 10H20" -> User : "2")**.


