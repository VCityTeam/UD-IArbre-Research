### Code Python pour calculer l'IBK

import numpy as np
import matplotlib.pyplot as plt

def generate_synthetic_slope_mnt(size):
    mnt = np.zeros((size, size))
    center = size // 2
    hole_size = 10  # Half of the 20x20 hole size

    for i in range(size):
        for j in range(size):
            if center - hole_size <= i < center + hole_size and center - hole_size <= j < center + hole_size:
                mnt[i, j] = 0  # Hole in the middle
            else:
                distance_to_center = np.sqrt((i - center)**2 + (j - center)**2)
                mnt[i, j] = distance_to_center  # Varied slope towards the hole

    return mnt

# Calculer la pente locale
def calculate_slope(mnt):
    grad_x, grad_y = np.gradient(mnt)
    slope = np.sqrt(grad_x**2 + grad_y**2)
    return slope

# Calculer la surface drainée (simplifiée pour cet exemple)
def calculate_drainage_area(mnt):
    drainage_area = np.ones_like(mnt)
    for i in range(1, mnt.shape[0] - 1):
        for j in range(1, mnt.shape[1] - 1):
            drainage_area[i, j] += np.sum(mnt[i-1:i+2, j-1:j+2] < mnt[i, j])
    return drainage_area

# Calculer l'Indice de Beven Kirkby (IBK)
def calculate_ibk(mnt):
    slope = calculate_slope(mnt)
    drainage_area = calculate_drainage_area(mnt)
    
    # Éviter les divisions par zéro
    slope[slope == 0] = 1e-6
    
    # Calcul de l'IBK
    ibk = np.log(drainage_area / np.tan(slope))
    return ibk

# Exemple d'utilisation
mnt = generate_synthetic_slope_mnt(100)  # Générer un MNT synthétique de 100x100
ibk = calculate_ibk(mnt)

# Afficher les résultats
plt.imshow(ibk, cmap='viridis')
plt.colorbar(label='Indice de Beven Kirkby (IBK)')
plt.title('Carte de l\'Indice de Beven Kirkby (IBK)')
plt.show()