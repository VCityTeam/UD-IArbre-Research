import numpy as np
import geopandas as gpd

# Calcul de la surface drainée (méthode simplifiée)
def calculate_drainage_area(mnt):
    drainage_area = np.ones_like(mnt)
    
    for i in range(1, mnt.shape[0] - 1):
        for j in range(1, mnt.shape[1] - 1):
            # Comptabilise les voisins plus bas comme surface drainée
            drainage_area[i, j] += np.sum(mnt[i-1:i+2, j-1:j+2] < mnt[i, j]) 
    
    return drainage_area

def compute_slope(terrain):
    dzdx, dzdy = np.gradient(terrain)
    slope = np.sqrt(dzdx**2 + dzdy**2)
    slope_angle = np.arctan(slope) 
    return slope, slope_angle


# Calcul de l'Indice de Beven Kirkby (IBK)
def calculate_ibk(mnt):
    # Calcul de la pente locale

    slope, slope_angle = compute_slope(mnt)
    drainage_area = calculate_drainage_area(mnt)

    # Éviter les divisions par zéro
    slope[slope == 0] = 1e-6

    # Calcul de l'IBK
    ibk = np.log(drainage_area / np.tan(slope_angle)+1e-6)
    return ibk, drainage_area

# Détection des cours d'eau ~ 
def extract_rivers(mnt):
    drainage_area = calculate_drainage_area(mnt)
    
    # Ajustement dynamique du seuil
    threshold = np.percentile(drainage_area, 99.1)  # Seuil basé sur la distribution des valeurs
    rivers = drainage_area > threshold  
    return rivers

# Détection des bassins versants
def extract_watersheds(mnt):
    drainage_area = calculate_drainage_area(mnt)

    watersheds = drainage_area > np.percentile(drainage_area, 85)
    return watersheds

def calculate_constant_charge(Q, L, A, h, t):
    # Calcul de perméabilité à charge constante (pour sols granulaires avec perméabilité élevée)
    """
    k : coefficient de perméabilité (m/s)
    Q : Volume d'eau écoulé (m^3)
    L : Longueur de l'échantillon (m)
    A : Section de l'échantillon (m^2)
    h : Différence de charge hydraulique (m)
    t : Temps de mesure (s)
    """
    return (Q * L) / (A * h * t)

def calculate_variable_charge(a, L, A, t, h1, h2):
    # Calcul de perméabilité à charge variable (pour sols fins avec perméabilité faible)
    """
    k : coefficient de perméabilité (m/s)
    a : section du tube gradué (m²)
    L : longueur de l'échantillon (m)
    A : section de l'échantillon (m²)
    t : durée entre deux mesures (s)
    h₁ : hauteur initiale (m)
    h₂ : hauteur finale (m)
    """
    return ((a * L) / (A * t)) * np.log(h1 / h2)

def calculate_terzaghi_empirical_formula(C, d, e):
    # Pas trouvé de source, à voir et vérifier
    # Calcul de perméabilité par la formule empirique de Terzaghi
    """
    k : coefficient de perméabilité (cm/s)
    C : constante empirique
    d : diamètre des particules de sol effectives (cm)
    e : indice des vides
    """
    return C * d^2 * ((e^3) / (1 + e))