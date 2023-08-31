# -*- coding: utf-8 -*-
# !/usr/bin/python3

import logging
import os
import traceback
import datetime
import json
import requests
import yagmail
import math
from Misc import get911, sendErrorEmail


def getFogosInfo():
    """
    Fetches and processes fire information from the Fogos API.

    Returns:
        list: A list of dictionaries containing useful fire information.
    """

    logger.info("Getting Fogos JSON Info")
    # Get Fogos JSON
    fogosJSON = json.loads(requests.get("https://api-dev.fogos.pt/new/fires").content)

    # Check if JSON is valid
    if not fogosJSON["success"]:
        logger.error("Failed to get Fogos JSON")
        return False

    # Get only useful info
    usefulFogos = []
    for fogo in fogosJSON["data"]:
        distance = haversine_distance(CENTER_POINT, (fogo["lat"], fogo["lng"]))
        isLocation = any(loc in fogo["location"] for loc in LOCATIONS)
        if distance <= MAX_DISTANCE or isLocation:
            usefulFogos.append({
                "id": int(fogo["id"]),
                "datetime": datetime.datetime.strptime(fogo["date"] + " " + fogo["hour"], "%d-%m-%Y %H:%M").strftime("%Y-%m-%d %H:%M"),
                "status": fogo["status"],
                "district": fogo["district"],
                "concelho": fogo["concelho"],
                "freguesia": fogo["freguesia"],
                "detailLocation": fogo["detailLocation"],
                "distancia": distance,
                "man": int(fogo["man"]),
                "terrain": int(fogo["terrain"]),
                "meios_aquaticos": int(fogo["meios_aquaticos"]),
                "natureza": fogo["natureza"]
            })

    return usefulFogos


def haversine_distance(coord1, coord2):
    """
    Calculate the haversine distance between two geographical coordinates.

    Args:
        coord1 (tuple): Latitude and longitude of the first point.
        coord2 (tuple): Latitude and longitude of the second point.

    Returns:
        float: The distance in kilometers between the two coordinates.
    """

    # Coordinates are in (latitude, longitude) format
    lat1, lon1 = coord1
    lat2, lon2 = coord2

    # Radius of the Earth in kilometers
    earth_radius = 6371.0

    # Convert latitude and longitude from degrees to radians
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    # Haversine formula
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = earth_radius * c

    return round(distance, 2)


def find_new_entries(new_data, saved_data):
    """
    Compare new data with saved data and find entries that are not present in the saved data.

    Args:
        new_data (list): List of dictionaries containing new data entries.
        saved_data (list): List of dictionaries containing saved data entries.

    Returns:
        list: A list of dictionaries representing new data entries that are not present in saved_data.
    """

    new_entries = []

    for new_entry in new_data:
        new_entry_id = new_entry["id"]
        found = False

        for saved_entry in saved_data:
            if new_entry_id == saved_entry["id"]:
                found = True
                break

        if not found:
            new_entries.append(new_entry)

    return new_entries


def find_updated_entries(new_data, saved_data):
    """
    Compare new data with saved data and find entries with updated information.

    Args:
        new_data (list): List of dictionaries containing new data entries.
        saved_data (list): List of dictionaries containing saved data entries.

    Returns:
        list: A list of dictionaries representing updated entries along with the updated keys and their old/new values.
    """

    updated_entries = []

    for new_entry in new_data:
        for saved_entry in saved_data:
            if new_entry["id"] == saved_entry["id"]:
                updated_keys = [
                    {key: {"old": saved_entry[key], "new": new_entry[key]}}
                    for key in new_entry
                    if new_entry[key] != saved_entry[key]
                ]
                if updated_keys:
                    updated_entries.append(
                        {"new_entry": new_entry, "updated_keys": updated_keys}
                    )

    return updated_entries


def find_deleted_entries(new_data, saved_data):
    """
    Compare new data with saved data and find entries that are present in saved data but not in new data.

    Args:
        new_data (list): List of dictionaries containing new data entries.
        saved_data (list): List of dictionaries containing saved data entries.

    Returns:
        list: A list of dictionaries representing entries that are present in saved_data but not in new_data.
    """

    deleted_entries = []

    for saved_entry in saved_data:
        saved_entry_id = saved_entry["id"]
        found = False

        for new_entry in new_data:
            if saved_entry_id == new_entry["id"]:
                found = True
                break

        if not found:
            deleted_entries.append(saved_entry)

    return deleted_entries


def translateKeys(fogo):
    """
    Translate keys of a fire dictionary to a new set of keys.

    Args:
        fogo (dict): A dictionary containing fire information with original keys.

    Returns:
        dict: A dictionary with translated keys.
    """

    # Translate keys and update the dictionary
    fogo["Data"] = fogo.pop("datetime")
    fogo["Estado"] = fogo.pop("status")
    fogo["Distrito"] = fogo.pop("district")
    fogo["Concelho"] = fogo.pop("concelho")
    fogo["Freguesia"] = fogo.pop("freguesia")
    fogo["Local"] = fogo.pop("detailLocation")
    fogo["Distância (KM)"] = fogo.pop("distancia")
    fogo["Operacionais"] = fogo.pop("man")
    fogo["Meios Terrestres"] = fogo.pop("terrain")
    fogo["Meios Aquáticos"] = fogo.pop("meios_aquaticos")
    fogo["Natureza"] = fogo.pop("natureza")

    return fogo


def custom_capitalize(input_string):
    """
    Capitalize the first letter of each word while preserving the case of other letters.

    Args:
        input_string (str): The input string to be processed.

    Returns:
        str: A new string with the first letter of each word capitalized while maintaining the case of the rest of the letters.
    """

    output_words = []
    input_string = str(input_string)
    words = input_string.split()

    for word in words:
        if word:
            capitalized_word = word[0].upper() + word[1:]
            output_words.append(capitalized_word)
        else:
            output_words.append('')

    return ' '.join(output_words)


def main():
    """
    Main function to monitor and send notifications for changes in fire information.

    This function fetches live fire information, compares it with saved data,
    detects new, deleted, and updated entries, and sends notification emails
    for these changes.

    Returns:
        None
    """

    # Get Live fogos info
    liveFogosInfo = getFogosInfo()

    # Save liveFogosInfo
    with open(configFile, "w") as outFile:
        json.dump(config, outFile, indent=2)

    # Get differences
    logger.info("Getting differences between live and saved JSON")
    new_entries = find_new_entries(liveFogosInfo, SAVED_FOGOS)
    deleted_entries = find_deleted_entries(liveFogosInfo, SAVED_FOGOS)
    updated_entries = find_updated_entries(liveFogosInfo, SAVED_FOGOS)
    changedFogos = {"new": new_entries, "deleted": deleted_entries, "updated": updated_entries}

    # Send emails for changed fogos
    for typeOf, entries in changedFogos.items():

        # Iterate through each entry of the given type
        for fogo in entries:

            # Determine the subject based on the typeOf value
            subject = "NOVO FOGO" if typeOf == "new" else "TERMINADO FOGO" if typeOf == "deleted" else "UPDATE"

            # Handle updated entries
            if typeOf == "updated":
                # Iterate through updated keys in the entry
                for updatedKey in fogo["updated_keys"]:
                    for key, values in updatedKey.items():
                        # Format updated values with color highlighting
                        fogo["new_entry"][key] = "<span style='color: red;font-weight: bold;'>" + str(values["old"]) + "</span>" + " / " + "<span style='color: green;font-weight: bold;'>" + str(values["new"]) + "</span>"

                # Update the entry to reflect the changes
                fogo = fogo["new_entry"]

            # Translate dictionary keys using the translateKeys function
            fogo = translateKeys(fogo)

            # Construct the email subject with the location
            subject += " | " + fogo["Freguesia"] + " | " + fogo["id"]

            # Construct the email body with formatted key-value pairs
            body = "\n".join(["<b>" + custom_capitalize(key) + "</b>" + " - " + custom_capitalize(val) for key, val in fogo.items()])

            # Send the email using yagmail library
            logger.info("Send email - " + subject)
            yagmail.SMTP(EMAIL_USER, EMAIL_APPPW).send(EMAIL_RECEIVER, subject, body)

    return


if __name__ == '__main__':
    # Set Logging
    LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.abspath(__file__).replace(".py", ".log"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
    logger = logging.getLogger()

    logger.info("----------------------------------------------------")

    # Set email settings
    EMAIL_USER = get911('EMAIL_USER')
    EMAIL_APPPW = get911('EMAIL_APPPW')
    EMAIL_RECEIVER = get911('EMAIL_RECEIVER')

    # Load Config File
    configFile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    with open(configFile, "r") as inFile:
        config = json.loads(inFile.read())
    MAX_DISTANCE = config["MAX_DISTANCE"]
    CENTER_POINT = (config["CENTER_POINT"]["LAT"], config["CENTER_POINT"]["LONG"])
    LOCATIONS = config["LOCATIONS"]
    SAVED_FOGOS = config["SAVED_FOGOS"]

    # Main
    try:
        main()
    except Exception as ex:
        logger.error(traceback.format_exc())
        sendErrorEmail(os.path.basename(__file__), str(traceback.format_exc()))
    finally:
        logger.info("End")
