# -*- coding: utf-8 -*-
# !/usr/bin/python3


import logging
import os
import traceback
import datetime
import json
import requests
import yagmail
from Misc import get911


def getFogosInfo():
    logger.info("Getting Fogos JSON Info")
    # Get Fogos JSON
    fogosJSON = json.loads(requests.get("https://api-dev.fogos.pt/new/fires").content)

    # Check if JSON is valid
    if not fogosJSON["success"]:
        logger.error("Failed to get Fogos JSON")
        return False

    # Get all Fogos
    locations = ["Óbidos", "Caldas da Rainha"]
    districtFogos = [fogo for fogo in fogosJSON["data"] if any(location in fogo["location"] for location in locations)]

    # Get only useful info
    usefulFogos = []
    for fogo in districtFogos:
        usefulFogos.append({
            "id": int(fogo["id"]),
            "datetime": datetime.datetime.strptime(fogo["date"] + " " + fogo["hour"], "%d-%m-%Y %H:%M").strftime("%Y-%m-%d %H:%M"),
            "status": fogo["status"],
            "location": fogo["location"],
            "man": int(fogo["man"]),
            "terrain": int(fogo["terrain"]),
            "meios_aquaticos": int(fogo["meios_aquaticos"]),
            "natureza": fogo["natureza"],
        })

    return usefulFogos


def loadSavedFogos(savedFogosFile):
    logger.info("Loading Saved Fogos JSON Info")

    try:
        with open(savedFogosFile, "r") as inFile:
            savedFogosInfo = json.loads(inFile.read())
    except Exception as ex:
        logger.warning("Failed to load savedFogosFile")
        return False

    return savedFogosInfo


def find_new_entries(new_data, saved_data):
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
    updated_entries = []

    for new_entry in new_data:
        for saved_entry in saved_data:
            if new_entry["id"] == saved_entry["id"]:
                updated_keys = [{key: {"old": saved_entry[key], "new": new_entry[key]}} for key in new_entry if new_entry[key] != saved_entry[key]]
                if updated_keys:
                    updated_entries.append({"new_entry": new_entry, "updated_keys": updated_keys})

    return updated_entries


def find_deleted_entries(new_data, saved_data):
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
    del fogo["id"]
    fogo["Data"] = fogo.pop("datetime")
    fogo["Estado"] = fogo.pop("status")
    fogo["Localização"] = fogo.pop("location")
    fogo["Operacionais"] = fogo.pop("man")
    fogo["Terrestres"] = fogo.pop("terrain")
    fogo["Meios Aquáticos"] = fogo.pop("meios_aquaticos")
    fogo["Natureza"] = fogo.pop("natureza")
    return fogo


def main():
    savedFogosFile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fogos.json")

    # Get Live fogos info
    liveFogosInfo = getFogosInfo()

    # Get saved fogos info
    savedFogosInfo = loadSavedFogos(savedFogosFile)
    savedFogosInfo = liveFogosInfo if not savedFogosInfo else savedFogosInfo

    # Save liveFogosInfo
    if os.path.exists(savedFogosFile):
        os.remove(savedFogosFile)
    with open(savedFogosFile, "w") as outFile:
        json.dump(liveFogosInfo, outFile, indent=2)

    # Get differences
    logger.info("Getting differences between live and saved JSON")
    new_entries = find_new_entries(liveFogosInfo, savedFogosInfo)
    deleted_entries = find_deleted_entries(liveFogosInfo, savedFogosInfo)
    updated_entries = find_updated_entries(liveFogosInfo, savedFogosInfo)
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
            subject += " - " + fogo["Localização"]

            # Construct the email body with formatted key-value pairs
            body = "\n".join(["<b>" + str(key).capitalize() + "</b>" + " - " + str(val) for key, val in fogo.items()])

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

    EMAIL_USER = get911('EMAIL_USER')
    EMAIL_APPPW = get911('EMAIL_APPPW')
    EMAIL_RECEIVER = get911('EMAIL_RECEIVER')

    # Main
    try:
        main()
    except Exception as ex:
        logger.error(traceback.format_exc())
        yagmail.SMTP(EMAIL_USER, EMAIL_APPPW).send(EMAIL_RECEIVER, "Error - " + os.path.basename(__file__), str(traceback.format_exc()))
    finally:
        logger.info("End")
