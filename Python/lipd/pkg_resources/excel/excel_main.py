import csv
import shutil
import xlrd
import copy
import re
import os
from collections import OrderedDict

from ..doi.doi_resolver import DOIResolver
from ..helpers.bag import finish_bag
from ..helpers.directory import dir_cleanup, create_tmp_dir, list_files
from ..helpers.zips import zipper
from ..helpers.loggers import create_logger
from ..helpers.blanks import EMPTY
from ..helpers.alternates import EXCEL_GEO, EXCEL_TEMPLATE, ALTS_MV, SHEETS, EXCEL_KEYS, EXCEL_HEADER
from ..helpers.regexes import re_sheet, re_var_w_units
from ..helpers.misc import normalize_name
from ..helpers.jsons import write_json_to_file

logger_excel = create_logger('excel_main')
"""
VERSION: LiPD v1.2
"""

def excel_main(files):
    """
    Parse data from Excel spreadsheets into LiPD files.
    :return:
    """

    # Find excel files
    print("Found " + str(len(files[".xls"])) + " Excel files")
    logger_excel.info("found excel files: {}".format(len(files[".xls"])))

    # Run once for each file
    for file in files[".xls"]:
        name_ext = file["filename_ext"]

        # Filename without extension
        name = file["filename_no_ext"]
        # remove foreign characters to prevent wiki uploading erros
        name = normalize_name(name)
        name_lpd = name + '.lpd'
        print("processing: {}".format(name_ext))
        logger_excel.info("processing: {}".format(name_ext))

        # Create a temporary folder and set paths
        dir_tmp = create_tmp_dir()
        dir_bag = os.path.join(dir_tmp, name)
        dir_data = os.path.join(dir_bag, 'data')

        # Make folders in tmp
        os.mkdir(os.path.join(dir_bag))
        os.mkdir(os.path.join(dir_data))

        pending_csv = []
        final = OrderedDict()
        logger_excel.info("variables initialized")

        """
        EACH DATA TABLE WILL BE STRUCTURED LIKE THIS
        "paleo_chron": "paleo",
        "pc_idx": 1,
        "model_idx": "",
        "table_type": "measurement",
        "table_idx": 1,
        "name": sheet,
        "filename": sheet,
        "data": { column data }
        """

        # Open excel workbook with filename
        try:
            workbook = xlrd.open_workbook(name_ext)
            logger_excel.info("opened XLRD workbook")

        except Exception as e:
            # There was a problem opening a file with XLRD
            print("Failed to open Workbook: {}".format(name))
            workbook = None
            logger_excel.debug("excel: xlrd failed to open workbook: {}, {}".format(name, e))

        if workbook:

            sheets, ct_paleo, ct_chron, metadata_str = _get_sheet_metadata(workbook, name)

            # METADATA WORKSHEETS
            # Parse Metadata sheet and add to output dictionary
            if metadata_str:
                logger_excel.info("parsing worksheet: {}".format(metadata_str))
                cells_dn_meta(workbook, metadata_str, 0, 0, final)

            # PALEO AND CHRON SHEETS
            for sheet in sheets:
                logger_excel.info("parsing data worksheet: {}".format(sheet["new_name"]))
                sheet_meta, sheet_csv = _parse_sheet(workbook, sheet)
                if sheet_csv and sheet_meta:
                    pending_csv.append(sheet_csv)
                    sheet["data"] = sheet_meta

            # create the metadata skeleton where we will place the tables. dynamically add empty table blocks for data.
            skeleton_paleo, skeleton_chron = _create_skeleton_main(sheets)

            # Reorganize sheet metadata into LiPD structure
            d_paleo, d_chron = _place_tables_main(sheets, skeleton_paleo, skeleton_chron)

            # Add organized metadata into final dictionary
            final['paleoData'] = d_paleo
            final['chronData'] = d_chron

            # OUTPUT

            # Create new files and dump data in dir_data
            os.chdir(dir_data)

            # WRITE CSV
            _write_data_csv(pending_csv)

            # JSON-LD
            # Invoke DOI Resolver Class to update publisher data
            try:
                logger_excel.info("invoking doi resolver")
                final = DOIResolver(file["dir"], name, final).main()
            except Exception as e:
                print("Error: doi resolver failed: {}".format(name))
                logger_excel.debug("excel: doi resolver failed: {}, {}".format(name, e))

            # Dump final_dict to a json file.
            final["LiPDVersion"] = 1.2
            write_json_to_file(name, final)

            # Move files to bag root for re-bagging
            # dir : dir_data -> dir_bag
            logger_excel.info("start cleanup")
            dir_cleanup(dir_bag, dir_data)

            # Create a bag for the 3 files
            finish_bag(dir_bag)

            # dir: dir_tmp -> dir_root
            os.chdir(file["dir"])

            # Check if same lpd file exists. If so, delete so new one can be made
            if os.path.isfile(name_lpd):
                os.remove(name_lpd)

            # Zip dir_bag. Creates in dir_root directory
            logger_excel.info("re-zip and rename")
            zipper(dir_tmp, name, name_lpd)

        # Move back to dir_root for next loop.
        os.chdir(file["dir"])

        # Cleanup and remove tmp directory
        shutil.rmtree(dir_tmp)

    print("Process Complete")
    return


# SORT THROUGH WORKSHEETS

def _get_sheet_metadata(workbook, name):
    """
    Get worksheet metadata. The sheet names tell us what type of table it is and where in the LiPD structure the data
    should be placed.

    Example VALID sheet name:
    paleo1measurement1
    paleo1model1ensemble
    paleo1model1distribution1

    Example INVALID sheet names:
    paleo1measurement
    paleo1model1ensemble1
    paleo1ensemble
    paleo1_measurement1

    NOTE: since each model will only have one ensemble and one summary, they should not have a trailing index number.

    :param obj workbook: Excel workbook
    :param str name: Dataset name
    :return dict int int str:
    """
    ct_paleo = 1
    ct_chron = 1
    metadata_str = ""
    sheets = []

    # Check what worksheets are available, so we know how to proceed.
    for sheet in workbook.sheet_names():

        # Use this for when we are dealing with older naming styles of "data (qc), data, and chronology"
        old = "".join(sheet.lower().strip().split())

        # Don't parse example sheets. For user use only.
        if "example" not in sheet and "sample" not in sheet:

            # Group the related sheets together, so it's easier to place in the metadata later.
            if 'metadata' in sheet.lower():
                metadata_str = sheet

            # Skip the 'about' and 'proxy' sheets altogether. Proceed with all other sheets.
            elif "about" not in sheet.lower() and "proxy" not in sheet.lower():
                logger_excel.info("creating sheets metadata")

                # If this is a valid sheet name, we will receive a regex object back.
                m = re.match(re_sheet, sheet.lower())
                print(m.groups())

                # Valid regex object. This is a valid sheet name and we can use that to build the sheet metdata.
                if m:
                    sheets, paleo_ct, chron_ct = _sheet_meta_from_regex(m, sheets, sheet, name, ct_paleo, ct_chron)

                # Older excel template style: backwards compatibility. Hard coded for one sheet per table.
                elif old == "data" or old in "data(qc)":
                    sheets.append({
                        "paleo_chron": "paleo",
                        "idx_pc": ct_paleo,
                        "idx_model": None,
                        "table_type": "measurement",
                        "idx_table": 1,
                        "old_name": sheet,
                        "new_name": sheet,
                        "filename": "{}paleo{}measurementTable1.csv".format(name, ct_paleo),
                        "table_name": "paleo{}measurementTable1".format(ct_paleo),
                        "data": ""
                    })
                    ct_paleo += 1

                # Older excel template style: backwards compatibility. Hard coded for one sheet per table.
                elif old == "chronology":
                    sheets.append({
                        "paleo_chron": "chron",
                        "idx_pc": ct_chron,
                        "idx_model": None,
                        "table_type": "measurement",
                        "idx_table": 1,
                        "old_name": sheet,
                        "new_name": sheet,
                        "filename": "{}chron{}measurementTable1.csv".format(name, ct_chron),
                        "table_name": "chron{}measurementTable1".format(ct_chron),
                        "data": ""
                    })
                    ct_chron += 1
                else:
                    # Sheet name does not conform to standard. Guide user to create a standardized sheet name.
                    print("This sheet name does not conform to naming standard: {}".format(sheet))
                    sheets, paleo_ct, chron_ct = _sheet_meta_from_prompts(sheets, sheet, name, ct_paleo, ct_chron)

    return sheets, ct_paleo, ct_chron, metadata_str


def _sheet_meta_from_prompts(sheets, old_name, name, ct_paleo, ct_chron):
    """
    Guide the user to create a proper, standardized sheet name
    :param list sheets: Running list of sheet metadata
    :param str old_name: Original sheet name
    :param str name: Data set name
    :param int ct_paleo: Running count of paleoData tables
    :param int ct_chron: Running count of chronData tables
    :return sheets paleo_ct chron_ct: Updated sheets and counts
    """
    cont = True
    # Loop until valid sheet name is built, or user gives up
    while cont:
        pc = input("Is this a (p)aleo or (c)hronology sheet?").lower()
        if pc in ("p", "c", "paleo", "chron", "chronology"):
            tt = input("Is this a (d)istribution, (e)nsemble, (m)easurement, or (s)ummary sheet?").lower()
            if tt in SHEETS["distribution"] or tt in SHEETS["ensemble"] \
                    or tt in SHEETS["summary"] or tt in SHEETS["measurement"]:
                # valid answer, keep going
                if tt in SHEETS["distribution"]:
                    tt = "distribution"
                elif tt in SHEETS["summary"]:
                    tt = "summary"
                elif tt in SHEETS["ensemble"]:
                    tt = "ensemble"
                elif tt in SHEETS["measurement"]:
                    tt = "measurement"

                if pc in SHEETS["paleo"]:
                    if tt in ["ensemble", "summary"]:
                        sheet = "{}{}{}{}".format("paleo", ct_paleo, tt, 1)
                    else:
                        sheet = "{}{}{}".format("paleo", ct_paleo, tt)
                elif pc in SHEETS["chron"]:
                    if tt in ["ensemble", "summary"]:
                        sheet = "{}{}{}{}".format("chron", ct_chron, tt, 1)
                    else:
                        sheet = "{}{}{}".format("chron", ct_chron, tt)
                # Test the sheet that was built from the user responses.
                # If it matches the Regex, then continue to build the sheet metadata. If not, try again or skip sheet.
                m = re.match(re_sheet, sheet.lower())
                if m:
                    sheets, ct_paleo, ct_chron = _sheet_meta_from_regex(m, sheets, old_name, name, ct_paleo, ct_chron)
                    print("Sheet created: {}".format(sheet))
                    cont = False
                else:
                    resp = input("invalid sheet name. try again? (y/n): ")
                    if resp == "n":
                        print("No valid sheet name was created. Skipping sheet: {}".format(sheet))
                        cont = False

    print("=====================================================")
    return sheets, ct_paleo, ct_chron


def _sheet_meta_from_regex(m, sheets, old_name, name, ct_paleo, ct_chron):
    """
    Build metadata for a sheet. Receive valid regex match object and use that to create metadata.
    :param obj m: Regex match object
    :param list sheets: Running list of sheet metadata
    :param str old_name: Original sheet name
    :param str name: Data set name
    :param int ct_paleo: Running count of paleoData tables
    :param int ct_chron: Running count of chronData tables
    :return sheets paleo_ct chron_ct: Updated sheets and counts
    """
    idx_model = None
    idx_table = None
    pc = m.group(1)
    # Get the model idx number from string if it exists
    if m.group(3):
        idx_model = int(m.group(4))
    # check if there's an index (for distribution tables)
    if m.group(6):
        idx_table = int(m.group(6))
    # find out table type
    if pc == "paleodata" or pc == "paleo":
        pc = "paleo"
        ct_paleo += 1
    elif pc == "chrondata" or pc == "chron":
        pc = "chron"
        ct_chron += 1
    # build filename and table name strings. build table name first, then make filename from the table_name
    new_name = "{}{}".format(pc, m.group(2))
    if idx_model:
        new_name = "{}model{}".format(new_name, idx_model)
    new_name = "{}{}".format(new_name, m.group(5))
    if idx_table:
        new_name = "{}{}".format(new_name, m.group(6))
    filename = "{}.{}.csv".format(name, new_name)

    # Standard naming. This matches the regex and the sheet name is how we want it.
    # paleo/chron - idx - table_type - idx
    # Not sure what to do with m.group(2) yet
    # ex: m.groups() = [ paleo, 1, model, 1, ensemble, 1 ]
    try:
        sheets.append({
            "old_name": old_name,
            "new_name": new_name,
            "filename": filename,
            "paleo_chron": pc,
            "idx_pc": int(m.group(2)),
            "idx_model": idx_model,
            "idx_table": idx_table,
            "table_type": m.group(5),
            "data": ""
        })
    except Exception as e:
        print("error: build sheets")
        logger_excel.debug("excel: build_sheet: unable to build sheet, {}".format(e))

    return sheets, ct_paleo, ct_chron


def _place_tables_section(skeleton_section, sheet, keys_section):
    """
    Place data into skeleton for either a paleo or chron section.
    :param dict skeleton_section: Empty or current progress of skeleton w/ data
    :param dict sheet: Sheet metadata
    :param list keys_section: Paleo or Chron specific keys
    :return dict: Skeleton section full of data
    """
    new_name = sheet["new_name"]
    logger_excel.info("placing_tables_section: {}".format(new_name))
    # get all the sheet metadata needed for this function
    idx_pc = sheet["idx_pc"] - 1
    idx_model = sheet["idx_model"]
    idx_table = sheet["idx_table"]
    table_type = sheet["table_type"]
    data = sheet["data"]
    # paleoMeas or chronMeas key
    key_1 = keys_section[0]
    # paleoModel or chronModel key
    key_2 = keys_section[1]
    # Is this a measurement, or distribution table?
    if idx_table:
        # Yes, a table idx exists, so decrement it.
        idx_table = sheet["idx_table"] - 1
    # Is this a ensemble, dist, or summary table?
    if idx_model:
        # Yes, a model idx exists, so decrement it.
        idx_model -= 1
    # If it's measurement table, it goes in first.
    try:
        if table_type == "measurement":
            skeleton_section[idx_pc][key_1][idx_table] = data
        # Other types of tables go one step below
        elif table_type in ["ensemble", "distribution", "summary"]:
            if table_type == "summary":
                skeleton_section[idx_pc][key_2][idx_model]["summaryTable"] = data
            elif table_type == "ensemble":
                skeleton_section[idx_pc][key_2][idx_model]["ensembleTable"] = data
            elif table_type == "distribution":
                skeleton_section[idx_pc][key_2][idx_model]["distributionTable"][idx_table] = data
    except Exception as e:
        logger_excel.warn("excel: place_tables_section: Unable to place table {}, {}".format(new_name, e))

    return skeleton_section


def _place_tables_main(sheets, skeleton_paleo, skeleton_chron):
    """
    All the data has been parsed, skeletons have been created, now put the data into the skeletons.
    :param list sheets: All metadata needed to place sheet data into the LiPD structure
    :param dict skeleton_paleo: The empty skeleton where we will place data
    :param dict skeleton_chron: The empty skeleton where we will place data
    :return:
    """
    logger_excel.info("enter place_tables")

    for sheet in sheets:
        pc = sheet["paleo_chron"]
        if pc == "paleo":
            skeleton_paleo = _place_tables_section(skeleton_paleo, sheet, ["paleoMeasurementTable", "paleoModel"])
        elif pc == "chron":
            skeleton_chron = _place_tables_section(skeleton_chron, sheet, ["chronMeasurementTable", "chronModel"])

    # when returning, these should no longer be skeletons. They should be tables filled with data
    logger_excel.info("exit place_tables")
    return skeleton_paleo, skeleton_chron


def _get_table_counts(sheet, num_section):
    """
    Loop through sheet metadata and count how many of each table type is needed at each index.
    Example: 'paleo 1' needs {'2 measurement tables', '1 model table, with 1 summary, 1 ensemble, and 3 distributions'}
    :param dict sheet: Sheet metadata
    :param dict num_section: Rolling number counts of table types for each index.
    :return:
    """
    tt = sheet["table_type"]
    idx_pc = sheet["idx_pc"]
    idx_table = sheet["idx_table"]
    idx_model = sheet["idx_model"]

    # Have we started counters for this idx model yet??
    if idx_pc not in num_section:
        # No, create the counters and start tracking table counts
        num_section[idx_pc] = {"ct_meas": 0, "ct_model": 0, "ct_in_model": {}}

    # Compare indices to get the highest index number for this table type
    # If we have a higher number model index, then increment out models count

    # Is this a ens, dist, or summary table?
    if idx_model:
        # Yes it is.

        # Is this model idx higher than ours?
        if idx_model > num_section[idx_pc]["ct_model"]:
            # Yes. Now, we have N number of model tables.
            num_section[idx_pc]["ct_model"] = idx_model

        # Have we started counters for this idx model yet??
        if idx_model not in num_section[idx_pc]["ct_in_model"]:
            # No, create the counters and start tracking table counts.
            num_section[idx_pc]["ct_in_model"][idx_model] = {"ct_ens": 0, "ct_sum": 0, "ct_dist": 0}

    # Incrementer!
    # For the given table type, track the highest index number.
    # That number is how many tables we need to make of that type.  Ex. 'measurement4', we need to make 4 empty tables

    if tt == "measurement":
        # Is this meas table a higher idx?
        if idx_table > num_section[idx_pc]["ct_meas"]:
            # Yes, set this idx to the counter.
            num_section[idx_pc]["ct_meas"] = idx_table
    elif tt == "distribution":
        # Is this dist table a higher idx?
        if idx_table > num_section[idx_pc]["ct_in_model"][idx_model]["ct_dist"]:
            # Yes, set this idx to the counter.
            num_section[idx_pc]["ct_in_model"][idx_model]["ct_dist"] = idx_table
    elif tt == "summary":
        # Summary tables are not indexed. Only one per table.
        num_section[idx_pc]["ct_in_model"][idx_model]["ct_sum"] = 1
    elif tt == "ensemble":
        # Ensemble tables are not indexed. Only one per table.
        num_section[idx_pc]["ct_in_model"][idx_model]["ct_ens"] = 1

    return num_section


def _create_skeleton_inner_tables(pc, l, num_section):
    """
    Create the inner tables of paleoData/chronData. (meas, summary, dist, ensemble) based on the counts provided in
    num_section.
    :param str pc:
    :param list l:
    :param dict num_section:
    :return dict:
    """
    template_model = {"summaryTable": {}, "ensembleTable": {}, "distributionTable": []}
    pc_meas = "{}MeasurementTable".format(pc)
    pc_mod = "{}Model".format(pc)
    for idx1, table in num_section.items():
        try:
            # create N number of empty measurement tables
            l[idx1 - 1][pc_meas] = [None] * num_section[idx1]["ct_meas"]
            # create N number of empty model tables
            l[idx1 - 1][pc_mod] = [copy.deepcopy(template_model)] * num_section[idx1]["ct_model"]
            # create N number of empty model tables
            for idx2, nums in table["ct_in_model"].items():
                try:
                    dists = [None] * nums["ct_dist"]
                except IndexError as e:
                    dists = []
                    logger_excel.debug("excel: create_metadata_skeleton: paleo tables messed up, {}".format(e))
                l[idx1 - 1][pc_mod][idx2-1] = {"summaryTable": {}, "ensembleTable": {},
                                                   "distributionTable": dists}
        except IndexError as e:
            logger_excel.warn("create_skeleton_inner_tables: IndexError: {}".format(e))
        except KeyError as e:
            logger_excel.warn("create_skeleton_inner_tables: KeyError: {}".format(e))
    return l


def _create_skeleton_inner_main(l, pc, num_section, template):
    """
    Create the top part of the table (measurement, model), and then call fn to create tables inside of model
    :param list l: paleoData or chronData list of tables
    :param dict num_section: Number of tables needed for each table type
    :param dict template: The empty template for the measurement and model top section.
    :return list:  paleoData or chronData list of tables - full skeleton
    """
    try:
        # Create N number of paleoData/chronData tables.
        l = [copy.deepcopy(template)] * len(num_section)
        # Create the necessary tables inside of "model"
        l = _create_skeleton_inner_tables(pc, l, num_section)
    except Exception as e:
        logger_excel.warn("excel: create_skeleton_main: error duplicating template tables, {}".format(e))

    return l


def _create_skeleton_main(sheets):
    """
    Find out what the highest number index table is, and create a list of that length.
    :return list: Blank list of N indices
    """
    logger_excel.info("enter create_metadata_skeleton")

    # These are the template structures that will be repeated for each table.
    template_paleo = {"paleoMeasurementTable": [], "paleoModel": []}
    template_chron = {"chronMeasurementTable": [], "chronModel": []}

    num_chron = {}
    num_paleo = {}
    paleo = []
    chron = []

    # First, get the max number index for each list we need to build.
    for sheet in sheets:
        pc = sheet["paleo_chron"]

        # Tree for chron types
        if pc == "chron":
            num_chron = _get_table_counts(sheet, num_chron)
        elif pc == "paleo":
            num_paleo = _get_table_counts(sheet, num_paleo)

    paleo = _create_skeleton_inner_main(paleo, "paleo", num_paleo, template_paleo)
    chron = _create_skeleton_inner_main(chron, "chron", num_chron, template_chron)

    return paleo, chron


# PARSE


def _parse_sheet(workbook, sheet):
    """
    The universal spreadsheet parser. Parse chron or paleo tables of type ensemble/model/summary.
    :param str name: Filename
    :param obj workbook: Excel Workbook
    :param dict sheet: Sheet path and naming info
    :return dict dict: Table metadata and numeric data
    """
    logger_excel.info("enter parse_sheet: {}".format(sheet["old_name"]))

    # Open the sheet from the workbook
    temp_sheet = workbook.sheet_by_name(sheet["old_name"])
    filename = sheet["filename"]

    # Store table metadata and numeric data separately
    table_name = "{}DataTableName".format(sheet["paleo_chron"])

    # Organize our root table data
    table_metadata = OrderedDict()
    table_metadata[table_name] = sheet["new_name"]
    table_metadata['filename'] = filename
    table_metadata['missingValue'] = 'NaN'

    # Store all CSV in here by rows
    table_data = {filename: []}

    # Master list of all column metadata
    column_metadata = []

    # Index tracks which cells are being parsed
    num_col = 0
    num_row = 0
    nrows = temp_sheet.nrows
    col_total = 0

    # Tracks which "number" each metadata column is assigned
    col_add_ct = 1

    header_keys = []
    variable_keys = []
    variable_keys_lower = []
    mv = ""

    # Markers to track where we are on the sheet
    var_header_done = False
    metadata_on = False
    metadata_done = False
    data_on = False
    notes = False

    try:
        # Loop for every row in the sheet
        for i in range(0, nrows):
            # Hold the contents of the current cell
            cell = temp_sheet.cell_value(num_row, num_col)
            row = temp_sheet.row(num_row)

            # Skip all template lines
            if isinstance(cell, str):
                # Note and missing value entries are rogue. They are not close to the other data entries.
                if cell.lower().strip() not in EXCEL_TEMPLATE:

                    if "notes" in cell.lower() and not metadata_on:
                        # Store at the root table level
                        nt = temp_sheet.cell_value(num_row, 1)
                        if nt not in EXCEL_TEMPLATE:
                            table_metadata["notes"] = nt

                    elif cell.lower().strip() in ALTS_MV:
                        # Store at the root table level and in our function
                        mv = temp_sheet.cell_value(num_row, 1)
                        # Add if not placeholder value
                        if mv not in EXCEL_TEMPLATE:
                            table_metadata["missingValue"] = mv

                    # Variable template header row
                    elif cell.lower() in EXCEL_HEADER and not metadata_on and not data_on:

                        # Grab the header line
                        row = temp_sheet.row(num_row)
                        header_keys = _get_header_keys(row)

                        # Turn on the marker
                        var_header_done = True

                    # Start parsing data section (bottom)
                    elif data_on:

                        # Parse the row, clean, and add to table_data
                        table_data = _parse_sheet_data_row(temp_sheet, num_row, col_total, table_data, filename, mv)

                    # Start parsing the metadata section. (top)
                    elif metadata_on:

                        # Reached an empty cell while parsing metadata. Mark the end of the section.
                        if cell in EMPTY:
                            metadata_on = False
                            metadata_done = True

                            # Create a list of all the variable names found
                            for entry in column_metadata:
                                try:
                                    # var keys is used as the variableName entry in each column's metadata
                                    variable_keys.append(entry["variableName"].strip())
                                    # var keys lower is used for comparing and finding the data header row
                                    variable_keys_lower.append(entry["variableName"].lower().strip())
                                except KeyError:
                                    # missing a variableName key
                                    pass

                        # Not at the end of the section yet. Parse the metadata
                        else:
                            # Get the row data
                            row = temp_sheet.row(num_row)

                            # Get column metadata
                            col_tmp = _compile_column_metadata(row, header_keys, col_add_ct)

                            # Append to master list
                            column_metadata.append(col_tmp)
                            col_add_ct += 1

                    # Variable metadata, if variable header exists
                    elif var_header_done and not metadata_done:

                        # Start piecing column metadata together with their respective variable keys
                        metadata_on = True

                        # Get the row data
                        row = temp_sheet.row(num_row)

                        # Get column metadata
                        col_tmp = _compile_column_metadata(row, header_keys, col_add_ct)

                        # Append to master list
                        column_metadata.append(col_tmp)
                        col_add_ct += 1

                    # Variable metadata, if variable header does not exist
                    elif not var_header_done and not metadata_done and cell:
                        # LiPD Version 1.1 and earlier: Chronology sheets don't have variable headers
                        # We could blindly parse, but without a header row_num we wouldn't know where
                        # to save the metadata
                        # Play it safe and assume data for first column only: variable name
                        metadata_on = True

                        # Get the row data
                        row = temp_sheet.row(num_row)

                        # Get column metadata
                        col_tmp = _compile_column_metadata(row, header_keys, col_add_ct)

                        # Append to master list
                        column_metadata.append(col_tmp)
                        col_add_ct += 1

                    # Data variable header row. Column metadata exists and metadata_done marker is on.
                    # This is where we compare top section variableNames to bottom section variableNames to see if
                    # we need to start parsing the column values
                    else:
                        try:
                            # Clean up variable_keys_lower so we all variable names change from "age(yrs BP)" to "age"
                            # Units in parenthesis make it too difficult to compare variables. Remove them.
                            row = _rm_units_from_var_names_multi(row)

                            if metadata_done and any(i in row for i in variable_keys_lower):
                                data_on = True
                                col_total = len(column_metadata)
                        except AttributeError:
                            pass
                            # cell is not a string, and lower() was not a valid call.

            # If this is a numeric cell, 99% chance it's parsing the data columns.
            elif isinstance(cell, float) or isinstance(cell, int):
                if data_on or metadata_done:

                    # Parse the row, clean, and add to table_data
                    table_data = _parse_sheet_data_row(temp_sheet, num_row, col_total, table_data, filename, mv)

            # Move on to the next row
            num_row += 1
        table_metadata["columns"] = column_metadata
    except IndexError as e:
        logger_excel.debug("parse_sheet: IndexError: sheet: {}, row_num: {}, col_num: {}, {}".format(sheet, num_row, num_col, e))

    # If there isn't any data in this sheet, and nothing was parsed, don't let this
    # move forward to final output.
    if not table_data[filename]:
        table_data = None
        table_metadata = None

    logger_excel.info("exit parse_sheet")
    return table_metadata, table_data


def _parse_sheet_data_row(temp_sheet, row_num, col_total, table_data, filename, mv):
    """
    Parse a row from the data section of the sheet. Add the cleaned row data to the overall table data.
    :param obj temp_sheet: Excel sheet
    :param int row_num: Current sheet row
    :param int col_total: Number of column variables in this sheet
    :param dict table_data: Running record of table data
    :param str filename: Filename for this table
    :param str mv: Missing value
    :return dict: Table data with appended row
    """
    # Get row of data
    row = temp_sheet.row(row_num)

    # In case our row holds more cells than the amount of columns we have, slice the row
    # We don't want to have extra empty cells in our output.
    row = row[:col_total]

    # Replace missing values where necessary
    row = _replace_mvs(row, mv)

    # Append row to list we will use to write out csv file later.
    table_data[filename].append(row)

    return table_data


def _replace_mvs(row, mv):
    """
    Replace Missing Values in the data rows where applicable
    :param list row: Row
    :return list: Modified row
    """
    for idx, v in enumerate(row):
        try:
            if v.value.lower() in EMPTY or v.value.lower() == mv:
                row[idx] = "NaN"
            else:
                row[idx] = v.value
        except AttributeError:
            if v.value == mv:
                row[idx] = "NaN"
            else:
                row[idx] = v.value

    return row


def _get_header_keys(row):
    """
    Get the variable header keys from this special row
    :return list: Header keys
    """
    # Swap out NOAA keys for LiPD keys
    for idx, key in enumerate(row):
        if key.value.lower() in EXCEL_KEYS:
            row[idx] = EXCEL_KEYS[key.value.lower()]
        else:
            try:
                row[idx] = key.value
            except AttributeError as e:
                logger_excel.warn("excel_main: get_header_keys: unknown header key, unable to add: {}".format(e))

    header_keys = _rm_cells_reverse(row)
    return header_keys


def _rm_units_from_var_name_single(var):
    """
    NOTE: USE THIS FOR SINGLE CELLS ONLY
    When parsing sheets, all variable names be exact matches when cross-referenceing the metadata and data sections
    However, sometimes people like to put "age (years BP)" in one section, and "age" in the other. This causes problems.
    We're using this regex to match all variableName cells and remove the "(years BP)" where applicable.
    :param str var: Variable name
    :return str: Variable name
    """
    # Use the regex to match the cell
    m = re.match(re_var_w_units, var)
    # Should always get a match, but be careful anyways.
    if m:
        # m.group(1): variableName
        # m.group(2): units in parenthesis (may not exist).
        try:
            var = m.group(1).strip().lower()
        except Exception:
            # This must be a malformed cell somehow. This regex should match every variableName cell.
            # It didn't work out. Return the original var as a fallback
            pass
    return var


def _rm_units_from_var_names_multi(row):
    """
    Wrapper around "_rm_units_from_var_name_single" for doing a list instead of a single cell.
    :param list row: Variable names
    :return list: Variable names
    """
    l2 = []
    # Check each var in the row
    for idx, var in enumerate(row):
        l2.append(_rm_units_from_var_name_single(row[idx].value))
    return l2


def _compile_column_metadata(row, keys, number):
    """
    Compile column metadata from one excel row ("9 part data")
    :param list row: Row of cells
    :param list keys: Variable header keys
    :return dict: Column metadata
    """
    # Store the variable keys by index in a dictionary
    column = {}

    # Use the header keys to place the column data in the dictionary
    if keys:
        for idx, key in enumerate(keys):
            try:
                val = row[idx].value
            except Exception:
                logger_excel.info("compile_column_metadata: Couldn't get value from row cell")
                val = "n/a"
            if key == "variableName":
                val = _rm_units_from_var_name_single(row[idx].value)
            column[key] = val
        column["number"] = number

    # If there are not keys, that means it's a header-less metadata section.
    else:
        # Assume we only have one cell, because we have no keys to know what data is here.
        try:
            val = row[0].value.lower()
        except AttributeError:
            val = row[0].value
        except Exception:
            logger_excel.info("compile_column_metadata: Couldn't get value from row cell")
            val = "n/a"
        val = _rm_units_from_var_name_single(val)
        column["variableName"] = val
        column["number"] = number

    # Add this column to the overall metadata
    column = {k: v for k, v in column.items() if v}
    return column


def _rm_cells_reverse(l):
    """
    Remove the cells that are empty or template in reverse order. Stop when you hit data.
    :param list l: One row from the spreadsheet
    :return list: Modified row
    """
    rm = []
    # Iter the list in reverse, and get rid of empty and template cells
    for idx, key in reversed(list(enumerate(l))):
        if key.lower() in EXCEL_TEMPLATE:
            rm.append(idx)
        elif key in EMPTY:
            rm.append(idx)
        else:
            break

    for idx in rm:
        l.pop(idx)
    return l


# CSV


def _write_data_csv(csv_data):
    """
    CSV data has been parsed by this point, so take it and write it file by file.
    :return:
    """
    logger_excel.info("enter write_data_csv")
    # Loop for each file and data that is stored
    for file in csv_data:
        for filename, data in file.items():

            # Make sure we're working with the right data types before trying to open and write a file
            if isinstance(filename, str) and isinstance(data, list):
                try:
                    with open(filename, 'w+') as f:
                        w = csv.writer(f)
                        for line in data:
                            w.writerow(line)
                except Exception:
                    logger_excel.debug("write_data_csv: Unable to open/write file: {}".format(filename))

    logger_excel.info("exit write_data_csv")
    return


# GEO DATA METHODS

def geometry_linestring(lat, lon, elev):
    """
    GeoJSON Linestring. Latitude and Longitude have 2 values each.
    :param list lat: Latitude values
    :param list lon:  Longitude values
    :return dict:
    """
    logger_excel.info("enter geometry_linestring")
    d = OrderedDict()
    coordinates = []
    temp = ["", ""]

    # Point type, Matching pairs.
    if lat[0] == lat[1] and lon[0] == lon[1]:
        logger_excel.info("matching geo coordinate")
        lat.pop()
        lon.pop()
        d = geometry_point(lat, lon, elev)

    else:
        # Creates coordinates list
        logger_excel.info("unique geo coordinates")
        for i in lon:
            temp[0] = i
            for j in lat:
                temp[1] = j
                coordinates.append(copy.copy(temp))
        if elev:
            for i in coordinates:
                i.append(elev)
        # Create geometry block
        d['type'] = 'Linestring'
        d['coordinates'] = coordinates
    logger_excel.info("exit geometry_linestring")
    return d


def geometry_range(crd_range, elev, crd_type):
    """
    Range of coordinates. (e.g. 2 latitude coordinates, and 0 longitude coordinates)
    :param crd_range: Latitude or Longitude values
    :param elev: Elevation value
    :param crd_type: Coordinate type, lat or lon
    :return dict:
    """

    d = OrderedDict()
    coordinates = [[] for i in range(len(crd_range))]

    # latitude
    if crd_type == "lat":
        for idx, i in enumerate(crd_range):
            coordinates[idx] = [crd_range[idx], "NaN"]
            if elev:
                coordinates[idx].append(elev)

    # longitude
    elif crd_type == "lon":
        for idx, i in enumerate(crd_range):
            coordinates[idx] = ["NaN", crd_range[idx]]
            if elev:
                coordinates[idx].append(elev)

    d["type"] = "Range"
    d["coordinates"] = coordinates

    return d


def geometry_point(lat, lon, elev):
    """
    GeoJSON point. Latitude and Longitude only have one value each
    :param list lat: Latitude values
    :param list lon: Longitude values
    :param float elev: Elevation value
    :return dict:
    """
    logger_excel.info("enter geometry_point")
    coordinates = []
    point_dict = OrderedDict()
    for idx, val in enumerate(lat):
        try:
            coordinates.append(lon[idx])
            coordinates.append(lat[idx])
        except IndexError as e:
            print("Error: Invalid geo coordinates")
            logger_excel.debug("geometry_point: IndexError: lat: {}, lon: {}, {}".format(lat, lon, e))

    coordinates.append(elev)
    point_dict['type'] = 'Point'
    point_dict['coordinates'] = coordinates
    logger_excel.info("exit geometry_point")
    return point_dict


def compile_geometry(lat, lon, elev):
    """
    Take in lists of lat and lon coordinates, and determine what geometry to create
    :param list lat: Latitude values
    :param list lon: Longitude values
    :param float elev: Elevation value
    :return dict:
    """
    logger_excel.info("enter compile_geometry")
    lat = _remove_geo_placeholders(lat)
    lon = _remove_geo_placeholders(lon)

    # 4 coordinate values
    if len(lat) == 2 and len(lon) == 2:
        logger_excel.info("found 4 coordinates")
        geo_dict = geometry_linestring(lat, lon, elev)

        # # 4 coordinate values
        # if (lat[0] != lat[1]) and (lon[0] != lon[1]):
        #     geo_dict = geometry_polygon(lat, lon)
        # # 3 unique coordinates
        # else:
        #     geo_dict = geometry_multipoint(lat, lon)
        #

    # 2 coordinate values
    elif len(lat) == 1 and len(lon) == 1:
        logger_excel.info("found 2 coordinates")
        geo_dict = geometry_point(lat, lon, elev)

    # coordinate range. one value given but not the other.
    elif (None in lon and None not in lat) or (len(lat) > 0 and len(lon) == 0):
        geo_dict = geometry_range(lat, elev, "lat")

    elif (None in lat and None not in lon) or (len(lon) > 0 and len(lat) == 0):
        geo_dict = geometry_range(lat, elev, "lon")

    # Too many points, or no points
    else:
        geo_dict = {}
        logger_excel.warn("compile_geometry: invalid coordinates: lat: {}, lon: {}".format(lat, lon))
    logger_excel.info("exit compile_geometry")
    return geo_dict


def compile_geo(d):
    """
    Compile top-level Geography dictionary.
    :param d:
    :return:
    """
    logger_excel.info("enter compile_geo")
    d2 = OrderedDict()

    # get max number of sites, or number of coordinate points given.
    num_loc = _get_num_locations(d)

    # if there's one more than one location put it in a collection
    if num_loc > 1:
        d2["type"] = "FeatureCollection"
        features = []
        for idx in range(0, num_loc):
            # Do process for one site
            site = _parse_geo_locations(d, idx)
            features.append(site)
        d2["features"] = features

    # if there's only one location
    elif num_loc == 1:
        d2 = _parse_geo_location(d)

    logger_excel.info("exit compile_geo")
    return d2


def _get_num_locations(d):
    """
    Find out how many locations are being parsed. Compare lengths of each
    coordinate list and return the max
    :param dict d: Geo metadata
    :return int: Max number of locations
    """
    lengths = []
    for key in EXCEL_GEO:
        try:
            if key != "siteName":
                lengths.append(len(d[key]))
        except Exception:
            lengths.append(1)

    try:
        num = max(lengths)
    except ValueError:
        num = 0
    return num


def _parse_geo_location(d):
    """
    Parse one geo location
    :param d:
    :return:
    """
    d2 = OrderedDict()
    filt = {}
    d2['type'] = 'Feature'
    # If the necessary keys are missing, put in placeholders so there's no KeyErrors.
    for key in EXCEL_GEO:
        if key not in d:
            d[key] = ""

    # Compile the geometry based on the info available.
    d2['geometry'] = compile_geometry([d['latMin'], d['latMax']], [d['lonMin'], d['lonMax']], d['elevation'])
    d2['properties'] = {'siteName': d['siteName']}

    return d2


def _parse_geo_locations(d, idx):
    """
    Parse one geo location
    :param d:
    :return:
    """
    d2 = OrderedDict()
    filt = {}
    d2['type'] = 'Feature'
    # If the necessary keys are missing, put in placeholders so there's no KeyErrors.
    for key in EXCEL_GEO:
        if key not in d:
            d[key] = ""

    for key in EXCEL_GEO:
        try:
            if key == "siteName" and isinstance(d["siteName"], str):
                filt["siteName"] = d["siteName"]
            else:
                filt[key] = d[key][idx]
        except KeyError:
            filt[key] = None
        except TypeError:
            filt[key] = None

    # Compile the geometry based on the info available.
    d2['geometry'] = compile_geometry([filt['latMin'], filt['latMax']], [filt['lonMin'], filt['lonMax']], filt['elevation'])
    d2['properties'] = {'siteName': filt['siteName']}

    return d2


def compile_authors(cell):
    """
    Split the string of author names into the BibJSON format.
    :param str cell: Data from author cell
    :return: (list of dicts) Author names
    """
    logger_excel.info("enter compile_authors")
    author_lst = []
    s = cell.split(';')
    for w in s:
        author_lst.append(w.lstrip())
    logger_excel.info("exit compile_authors")
    return author_lst


# MISC HELPER METHODS


def compile_temp(d, key, value):
    """
    Compiles temporary dictionaries for metadata. Adds a new entry to an existing dictionary.
    :param dict d:
    :param str key:
    :param any value:
    :return dict:
    """
    if not value:
        d[key] = None
    elif len(value) == 1:
        d[key] = value[0]
    else:
        d[key] = value
    return d


def compile_fund(workbook, sheet, row, col):
    """
    Compile funding entries. Iter both rows at the same time. Keep adding entries until both cells are empty.
    :param obj workbook:
    :param str sheet:
    :param int row:
    :param int col:
    :return list of dict: l
    """
    logger_excel.info("enter compile_fund")
    l = []
    temp_sheet = workbook.sheet_by_name(sheet)
    while col < temp_sheet.ncols:
        col += 1
        try:
            agency = temp_sheet.cell_value(row, col)
            grant = temp_sheet.cell_value(row+1, col)
            if (agency != xlrd.empty_cell and agency not in EMPTY) or (grant != xlrd.empty_cell and grant not in EMPTY):
                if agency in EMPTY:
                    l.append({'grant': grant})
                elif grant in EMPTY:
                    l.append({'agency': agency})
                else:
                    l.append({'agency': agency, 'grant': grant})
        except IndexError as e:
            logger_excel.debug("compile_fund: IndexError: sheet:{} row:{} col:{}, {}".format(sheet, row, col, e))
    logger_excel.info("exit compile_fund")
    return l


def name_to_jsonld(title_in):
    """
    Convert formal titles to camelcase json_ld text that matches our context file
    Keep a growing list of all titles that are being used in the json_ld context
    :param str title_in:
    :return str:
    """
    title_out = ''
    try:
        title_in = title_in.lower()
        title_out = EXCEL_KEYS[title_in]
    except (KeyError, AttributeError) as e:
        if "(" in title_in:
            title_in = title_in.split("(")[0].strip()
        # try to find an exact match first.
        for k, v in EXCEL_KEYS.items():
            if title_in == k:
                return v
        # if no exact match, find whatever is a closest match
        for k, v in EXCEL_KEYS.items():
            if k in title_in:
                return v
    if not title_out:
        logger_excel.debug("name_to_jsonld: No match found: {}".format(title_in))
    return title_out


def instance_str(cell):
    """
    Match data type and return string
    :param any cell:
    :return str:
    """
    if isinstance(cell, str):
        return 'str'
    elif isinstance(cell, int):
        return 'int'
    elif isinstance(cell, float):
        return 'float'
    else:
        return 'unknown'


def replace_mvs(cell_entry, missing_val):
    """
    The missing value standard is "NaN". If there are other missing values present, we need to swap them.
    :param str cell_entry: Contents of target cell
    :param str missing_val:
    :return str:
    """
    if isinstance(cell_entry, str):
        missing_val_list = ['none', 'na', '', '-', 'n/a']
        if missing_val.lower() not in missing_val_list:
            missing_val_list.append(missing_val)
        try:
            if cell_entry.lower() in missing_val_list:
                cell_entry = 'NaN'
        except (TypeError, AttributeError) as e:
            logger_excel.debug("replace_missing_vals: Type/AttrError: cell: {}, mv: {} , {}".format(cell_entry, missing_val, e))
    return cell_entry


def extract_units(string_in):
    """
    Extract units from parenthesis in a string. i.e. "elevation (meters)"
    :param str string_in:
    :return str:
    """
    start = '('
    stop = ')'
    return string_in[string_in.index(start) + 1:string_in.index(stop)]


def extract_short(string_in):
    """
    Extract the short name from a string that also has units.
    :param str string_in:
    :return str:
    """
    stop = '('
    return string_in[:string_in.index(stop)]


# DATA WORKSHEET HELPER METHODS


def cells_rt_meta_pub(workbook, sheet, row, col, pub_qty):
    """
    Publication section is special. It's possible there's more than one publication.
    :param obj workbook:
    :param str sheet:
    :param int row:
    :param int col:
    :param int pub_qty: Number of distinct publication sections in this file
    :return list: Cell data for a specific row
    """
    logger_excel.info("enter cells_rt_meta_pub")
    col_loop = 0
    cell_data = []
    temp_sheet = workbook.sheet_by_name(sheet)
    while col_loop < pub_qty:
        col += 1
        col_loop += 1
        cell_data.append(temp_sheet.cell_value(row, col))
    logger_excel.info("exit cells_rt_meta_pub")
    return cell_data


def cells_rt_meta(workbook, sheet, row, col):
    """
    Traverse all cells in a row. If you find new data in a cell, add it to the list.
    :param obj workbook:
    :param str sheet:
    :param int row:
    :param int col:
    :return list: Cell data for a specific row
    """
    logger_excel.info("enter cells_rt_meta")
    col_loop = 0
    cell_data = []
    temp_sheet = workbook.sheet_by_name(sheet)
    while col_loop < temp_sheet.ncols:
        col += 1
        col_loop += 1
        try:
            if temp_sheet.cell_value(row, col) != xlrd.empty_cell and temp_sheet.cell_value(row, col) != '':
                cell_data.append(temp_sheet.cell_value(row, col))
        except IndexError as e:
            logger_excel.warn("cells_rt_meta: IndexError: sheet: {}, row: {}, col: {}, {}".format(sheet, row, col, e))
    logger_excel.info("exit cells_right_meta")
    return cell_data


def cells_dn_meta(workbook, sheet, row, col, final_dict):
    """
    Traverse all cells in a column moving downward. Primarily created for the metadata sheet, but may use elsewhere.
    Check the cell title, and switch it to.
    :param obj workbook:
    :param str sheet:
    :param int row:
    :param int col:
    :param dict final_dict:
    :return: none
    """
    logger_excel.info("enter cells_dn_meta")
    row_loop = 0
    pub_cases = ['id', 'year', 'author', 'journal', 'issue', 'volume', 'title', 'pages',
                 'reportNumber', 'abstract', 'alternateCitation']
    geo_cases = ['latMin', 'lonMin', 'lonMax', 'latMax', 'elevation', 'siteName', 'location']

    # Temp
    pub_qty = 0
    geo_temp = {}
    general_temp = {}
    pub_temp = []
    funding_temp = []

    temp_sheet = workbook.sheet_by_name(sheet)

    # Loop until we hit the max rows in the sheet
    while row_loop < temp_sheet.nrows:
        try:
            # Get cell value
            cell = temp_sheet.cell_value(row, col)

            # If there is content in the cell...
            if cell not in EMPTY:

                # Convert title to correct format, and grab the cell data for that row
                title_formal = temp_sheet.cell_value(row, col)
                title_json = name_to_jsonld(title_formal)

                # If we don't have a title for it, then it's not information we want to grab
                if title_json:

                    # Geo
                    if title_json in geo_cases:
                        cell_data = cells_rt_meta(workbook, sheet, row, col)
                        geo_temp = compile_temp(geo_temp, title_json, cell_data)

                    # Pub
                    # Create a list of dicts. One for each pub column.
                    elif title_json in pub_cases:

                        # Authors seem to be the only consistent field we can rely on to determine number of Pubs.
                        if title_json == 'author':
                            cell_data = cells_rt_meta(workbook, sheet, row, col)
                            pub_qty = len(cell_data)
                            for i in range(pub_qty):
                                author_lst = compile_authors(cell_data[i])
                                pub_temp.append({'author': author_lst, 'pubDataUrl': 'Manually Entered'})
                        else:
                            cell_data = cells_rt_meta_pub(workbook, sheet, row, col, pub_qty)
                            for pub in range(pub_qty):
                                if title_json == 'id':
                                    pub_temp[pub]['identifier'] = [{"type": "doi", "id": cell_data[pub]}]
                                else:
                                    pub_temp[pub][title_json] = cell_data[pub]
                    # Funding
                    elif title_json == 'agency':
                        funding_temp = compile_fund(workbook, sheet, row, col)

                    # All other cases do not need fancy structuring
                    else:
                        cell_data = cells_rt_meta(workbook, sheet, row, col)
                        general_temp = compile_temp(general_temp, title_json, cell_data)

        except IndexError as e:
            logger_excel.debug("cells_dn_datasheets: IndexError: sheet: {}, row: {}, col: {}, {}".format(sheet, row, col, e))
        row += 1
        row_loop += 1

    # Compile the more complicated items
    geo = compile_geo(geo_temp)

    logger_excel.info("compile metadata dictionary")
    # Insert into final dictionary
    final_dict['@context'] = "context.jsonld"
    final_dict['pub'] = pub_temp
    final_dict['funding'] = funding_temp
    final_dict['geo'] = geo

    # Add remaining general items
    for k, v in general_temp.items():
        final_dict[k] = v
    logger_excel.info("exit cells_dn_meta")
    return


# CHRONOLOGY HELPER METHODS


def count_chron_variables(temp_sheet):
    """
    Count the number of chron variables
    :param obj temp_sheet:
    :return int: variable count
    """
    total_count = 0
    start_row = traverse_to_chron_var(temp_sheet)
    while temp_sheet.cell_value(start_row, 0) != '':
        total_count += 1
        start_row += 1
    return total_count


def get_chron_var(temp_sheet, start_row):
    """
    Capture all the vars in the chron sheet (for json-ld output)
    :param obj temp_sheet:
    :param int start_row:
    :return: (list of dict) column data
    """
    col_dict = OrderedDict()
    out_list = []
    column = 1

    while (temp_sheet.cell_value(start_row, 0) != '') and (start_row < temp_sheet.nrows):
        short_cell = temp_sheet.cell_value(start_row, 0)
        units_cell = temp_sheet.cell_value(start_row, 1)
        long_cell = temp_sheet.cell_value(start_row, 2)

        # Fill the dictionary for this column
        col_dict['number'] = column
        col_dict['variableName'] = short_cell
        col_dict['description'] = long_cell
        col_dict['units'] = units_cell
        out_list.append(col_dict.copy())
        start_row += 1
        column += 1

    return out_list


def traverse_to_chron_data(temp_sheet):
    """
    Traverse down to the first row that has chron data
    :param obj temp_sheet:
    :return int: traverse_row
    """
    traverse_row = traverse_to_chron_var(temp_sheet)
    reference_var = temp_sheet.cell_value(traverse_row, 0)

    # Traverse past all the short_names, until you hit a blank cell (the barrier)
    while temp_sheet.cell_value(traverse_row, 0) != '':
        traverse_row += 1
    # Traverse past the empty cells until we hit the chron data area
    while temp_sheet.cell_value(traverse_row, 0) == '':
        traverse_row += 1

    # Check if there is a header row. If there is, move past it. We don't want that data
    if temp_sheet.cell_value(traverse_row, 0) == reference_var:
        traverse_row += 1
    logger_excel.info("traverse_to_chron_data: row:{}".format(traverse_row))
    return traverse_row


def traverse_to_chron_var(temp_sheet):
    """
    Traverse down to the row that has the first variable
    :param obj temp_sheet:
    :return int:
    """
    row = 0
    while row < temp_sheet.nrows - 1:
        if 'Parameter' in temp_sheet.cell_value(row, 0):
            row += 1
            break
        row += 1
    logger_excel.info("traverse_to_chron_var: row:{}".format(row))
    return row


def get_chron_data(temp_sheet, row, total_vars):
    """
    Capture all data in for a specific chron data row (for csv output)
    :param obj temp_sheet:
    :param int row:
    :param int total_vars:
    :return list: data_row
    """
    data_row = []
    missing_val_list = ['none', 'na', '', '-']
    for i in range(0, total_vars):
        cell = temp_sheet.cell_value(row, i)
        if isinstance(cell, str):
            cell = cell.lower()
        if cell in missing_val_list:
            cell = 'NaN'
        data_row.append(cell)
    return data_row


def _remove_geo_placeholders(l):
    """
    Remove placeholders from coordinate lists and sort
    :param list l: Lat or long list
    :return list: Modified list
    """
    vals = []
    for i in l:
        if isinstance(i, list):
            for k in i:
                if isinstance(k, float) or isinstance(k, int):
                    vals.append(k)
        elif isinstance(i, float) or isinstance(i, int):
            vals.append(i)
    vals.sort()
    return vals


if __name__ == '__main__':
    excel_main()
