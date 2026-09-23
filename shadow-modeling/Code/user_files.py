from datetime import datetime
import requests
import json
from pathlib import Path

#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Author : Karima Ouadah < ouadkarima@outlook.com >
#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def user_sun_phase_choice(alpha_list, horaire_list):
    projection_type = 0
    single_projection = "NULL"
    if alpha_list[0] <= 0:
        start_index = 1
    else:
        start_index = 0

    while True:
        while projection_type == 0:
            single_projection = input("\nDo you want a single shadow projection? (y/n)")
            if single_projection == "y" or single_projection == "n":
                projection_type = 1

        if single_projection == "y":
            print("\nYou have to choose a single time of day.")
        if single_projection == "n":
            print("\nYou need to choose a start time and an end time for the period of the day.")
            print("\nThe shadows are darker or lighter depending on whether the area remained shaded for a long time during the selected period!")

        print("\nPlease choose a phase of the day (from sunrise to sunset):\n")

        for i in range(start_index, len(alpha_list)):
            print(f"{i} : {horaire_list[i]}")

        user_time_index = input("\nEnter the index of the start: ")
        if single_projection == "n":
            user_time_index_end = input("\nEnter the index of the end: ")
        else :
            user_time_index_end = 99

        if user_time_index.isdigit() and user_time_index_end.isdigit():
            user_time_index = int(user_time_index)
            user_time_index_end = int(user_time_index_end)

            if start_index <= user_time_index < len(alpha_list) and start_index <= user_time_index_end <= len(alpha_list):
                if user_time_index < user_time_index_end:
                    return user_time_index, user_time_index_end

        print(f"Invalid entry. Please enter a number between {start_index} and {len(alpha_list)-1}.")


def user_date_choice():
    while True:
        day = input("Please enter the day : ")
        month = input("Please enter the month : ")
        year = input("Please enter the year : ")
        print("\n")

        try:
            date = datetime(int(year), int(month), int(day))
            return date.year, date.month, date.day
        except ValueError:
            print("Invalid date. Please try again.")

def user_entry_data():
    user_x_min = input("Please enter x min: ")
    user_y_min = input("Please enter y min: ")

    return user_x_min, user_y_min

def user_laz_api(url_api, user_x_min, user_y_min):
    answer = requests.get(url_api, verify=False)
    data = answer.json()

    for value in data["values"]:
        if value["x_min"] == int(user_x_min) and value["y_min"] == int(user_y_min):
            url = value["url"].strip()
            break
    else:
        raise ValueError("Tile not found")

    r = requests.get(url, verify=False)
    r.raise_for_status()

    output_dir = Path("Data")
    output_dir.mkdir(exist_ok=True)
    filename = output_dir / f"tile_{user_x_min}_{user_y_min}.laz"

    with open(filename, "wb") as f:
        f.write(r.content)

    return str(filename)