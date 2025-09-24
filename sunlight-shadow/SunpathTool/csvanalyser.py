import matplotlib.pyplot as plt
import numpy as np
import csv, sys, os

rootdir = "testcsv"

lightedlist = []

for folder, subs, files in os.walk(rootdir):
    with open(os.path.join(folder, 'python-outfile.txt'), 'w') as dest:
        for filename in files:
            with open(os.path.join(folder, filename), 'r') as src:
                if filename == "output.csv":
                    csv_reader = csv.DictReader(src, delimiter=';')
                    line_count = 0
                    lighted = 0
                    for row in csv_reader:
                        if line_count == 0:
                            print(f'Column names are {", ".join(row)}')
                        elif row["lighted"] == "True":
                            lighted +=1
                        line_count += 1
                    lightedlist.append(lighted)

lightedlist = np.array(lightedlist)

plt.plot(lightedlist)
plt.show()
