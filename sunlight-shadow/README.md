# Sunlight and Shadow Analyses

## Team

* Marwan Ait-Addi
* John Samuel
* Gilles Gesquière

## Description

This project aims to provide tools to calculate sunlight exposure of city objects, using data in the 3DTiles format it is composed of multiple subprojects that all work together to form a coherent toolsuite.

1. [Sunlight](https://github.com/VCityTeam/Sunlight) This is the C++ library that is used by pySunlight *You don´t need to worry about to use pySunlight*
2. [pySunlight](https://github.com/VCityTeam/pySunlight)
3. [pySunlight-Docker](https://github.com/VCityTeam/pySunlight-docker) **Use this if you don´t already know where to start**
4. [UD-Sunlight-demo](https://github.com/VCityTeam/UD-Demo-Sunlight) A small web app for visualisation
5. The Sunpath calculation script that is compatible with the aforementioned tools. It can be found in the SunpathTool directory.
6. The technical repport that can be found in this folder.

Each projects' github repository contains its own documentation. As well as use cases and examples.
Each one is dependent on the previous project (pySunlight is dependent on Sunlight, and pySunlight-docker is a dockerised version of pySunlight).

The sunpath calculation script is optional, and only needed if you want to calculate sunlight outside of the pre-calculated 1975-2075 range.

## Cloning the submodules

This repository contains submodules, which need to be pulled (if you didn´t already specify --recusive when cloning the repo)

```bash
git clone --recursive [link] # If you haven´t cloned this repository yet

git submodule update --init # If the repository is already cloned
```
