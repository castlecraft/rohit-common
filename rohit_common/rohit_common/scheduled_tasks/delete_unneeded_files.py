# -*- coding: utf-8 -*-
# Copyright (c) 2020, Rohit Industries Group Pvt Ltd. and contributors
# For license information, please see license.txt

# This Scheduled Tasks Would check all files marked for Deletion and Delete them.
# It would also check the files which are attached to some doctypes which need to be deleted as per deletion policy

from __future__ import unicode_literals
import time
import frappe
from frappe.utils import flt
from frappe.utils.fixtures import sync_fixtures
from ...utils.rohit_common_utils import rebuild_tree
from rohit_common.core.file import check_file_availability, delete_file_dt, check_and_move_file, correct_file_name_url

sync_fixtures()


def execute():
    st_time = time.time()
    # Check and delete files without File Name and File URL
    print("Deleting Files without File Name and File URL")
    delete_files = frappe.db.sql("""SELECT name FROM `tabFile` WHERE file_name IS NULL
    AND file_url IS NULL""", as_dict=1)
    for files in delete_files:
        fd = frappe.get_doc("File", files.name)
        delete_file_dt(fd)
    print(f"Total Files Deleted due to no File Name and File URL {len(delete_files)}")
    print("Converting the Incorrect File Names and File URL for Files")
    incorrect_file_name = frappe.db.sql("""SELECT name FROM `tabFile` WHERE file_name IS NULL
    AND is_folder=0""", as_dict=1)
    for file in incorrect_file_name:
        fd = frappe.get_doc("File", file.name)
        correct_file_name_url(fd)
    print(f"Total Files Corrected with File Names {len(incorrect_file_name)}")
    null_file_url = frappe.db.sql("""SELECT name FROM `tabFile` WHERE file_url IS NULL
    AND is_folder=0""", as_dict=1)
    for file in null_file_url:
        fd = frappe.get_doc("File", file.name)
        correct_file_name_url(fd)
    frappe.db.commit()
    print(f"Total Files Corrected with NULL File URL {len(null_file_url)}")
    print(f"Checking the Files with http URL and Trying to Correct it")
    http_file_url = frappe.db.sql("""SELECT name FROM `tabFile` WHERE file_url LIKE 'http%'
    AND is_folder=0""", as_dict=1)
    for file in http_file_url:
        fd = frappe.get_doc("File", file.name)
        correct_file_name_url(fd)
    print(f"Total Files Corrected with http in File URL {len(http_file_url)}")
    frappe.db.commit()
    print("Checking for Archive Folders and Making All Files Under them Archive Files")
    time.sleep(2)
    archived = 0
    arch_folders = frappe.db.sql("""SELECT name, lft, rgt FROM `tabFile` WHERE is_folder=1
    AND important_document_for_archive=1 AND is_home_folder=0 AND is_attachments_folder=0""", as_dict=1)
    for folder in arch_folders:
        print(f"{folder.name} is a Archive Folder and Hence All files and Folders Under it Would be Archived")
        files = frappe.db.sql("""SELECT name, file_name FROM `tabFile` WHERE rgt <= %s
        AND lft >= %s AND important_document_for_archive = 0""" % (folder.rgt, folder.lft), as_dict=1)
        for file in files:
            print(f"{file.name} with File Name={file.file_name} is being made Archive File")
            fd = frappe.get_doc("File", file.name)
            fd.important_document_for_archive = 1
            fd.save()
    frappe.db.commit()
    print("Checking for Files Marked to Delete")
    time.sleep(1)
    marked = 0
    files_marked_to_delete = frappe.db.sql("""SELECT name, file_name, file_url, attached_to_doctype, attached_to_name
    FROM `tabFile` WHERE mark_for_deletion = 1""", as_dict=1)
    for file in files_marked_to_delete:
        marked += 1
        fd = frappe.get_doc("File", file.name)
        comment = f"Removed {file.name} as it was Marked for Deletion"
        delete_file_dt(fd, comment=comment)
        if marked % 500 == 0 and marked > 0:
            frappe.db.commit()
            print(f"Committing Changes after {marked} files Marked for Delete Deleted.")
    frappe.db.commit()
    mark_time = int(time.time() - st_time)

    # Now check the Doctypes in Settings for Auto Deletion Rule
    print("Checking for Auto Deletion of Files")
    time.sleep(1)
    ro_set = frappe.get_single("Rohit Settings")
    tot_auto_delete = 0
    auto_delete = 0
    for row in ro_set.auto_deletion_policy_for_files:
        dt_conds = ""
        if row.doctype_conditions:
            dt_conds = " AND %s" % row.doctype_conditions
        query = """SELECT fd.name FROM `tabFile` fd, `tab%s` dt WHERE fd.attached_to_doctype = '%s' AND
        fd.creation <= DATE_SUB(NOW(), INTERVAL %s DAY) AND dt.name = fd.attached_to_name %s""" \
                % (row.document_type, row.document_type, row.days_to_keep, dt_conds)
        files = frappe.db.sql(query, as_dict=1)
        tot_auto_delete += len(files)
        for file in files:
            auto_delete += 1
            fd = frappe.get_doc("File", file.name)
            doc = frappe.get_doc(row.document_type, fd.attached_to_name)
            comment = f"Removed {file.name} Due to Deletion Policy to Delete After {row.days_to_keep} Days"
            delete_file_dt(fd, comment=comment)
            if auto_delete % 500 == 0 and auto_delete > 0:
                frappe.db.commit()
                print(f"Committing Changes after {auto_delete} files Deleted. Total Time Elapsed "
                      f"{int(time.time() - st_time)} seconds")
    frappe.db.commit()
    del_time = int(time.time() - st_time)

    # Lastly Check all files in DB which are not Marked as Available on Server and Delete those files
    # Also note that if File is Public It would need to be converted into Private if Available and If attached to
    # Doctype where Public Files are not allowed
    print("Check for File Availability and Updating the Same")
    avail_count = 0
    non_validated_files = frappe.db.sql("""SELECT name FROM `tabFile` WHERE file_available_on_server = 0
    AND is_folder=0""", as_dict=1)
    print(f"Total Non Available Files = {len(non_validated_files)}")
    time.sleep(1)
    non_avail_files = 0
    non_avail_dt = 0
    for file in non_validated_files:
        avail_count += 1
        dont_save = 0
        # print(f"Checking {file.name}")
        fd = frappe.get_doc("File", file.name)
        file_available = check_file_availability(fd)
        if file_available == 1:
            pub_allowed = 0
            # File is available and now check if its public and attached to DT where Public Files are not allowed
            # Then change the file to private and also mark as available
            if fd.is_private != 1:
                if fd.attached_to_doctype:
                    for d in ro_set.docs_with_pub_att:
                        if d.document_type == fd.attached_to_doctype:
                            pub_allowed = 1
                            break
                    if pub_allowed != 1:
                        fd.file_available_on_server = 1
                        fd.is_private = 1
            if fd.attached_to_name:
                if not frappe.db.exists(fd.attached_to_doctype, fd.attached_to_name):
                    non_avail_dt += 1
                    dont_save = 1
                    delete_file_dt(fd, ref_doc_exists=0)
                    print(f"Deleting {fd.name} since Attached to Non-Existent Document")
            else:
                fd.file_available_on_server = 1
            if dont_save != 1:
                fd.save()
        elif file_available == 2:
            check_and_move_file(fd)
        else:
            file_exists = 0
            if fd.attached_to_doctype:
                if not frappe.db.exists(fd.attached_to_doctype, fd.attached_to_name):
                    file_exists = 0
                else:
                    file_exists = 1
            comment = f"File Removed Since Not Available on Server"
            delete_file_dt(fd, comment=comment, ref_doc_exists=file_exists)
        if avail_count % 500 == 0 and avail_count > 0:
            frappe.db.commit()
            print(f"Committing Changes after {avail_count} files made available Time Elapsed "
                  f"{int(time.time() - st_time)} seconds")
    avail_time = int(time.time() - st_time)

    tot_time = int(time.time() - st_time)
    print(f"Total Marked Files Deleted = {len(files_marked_to_delete)}")
    print(f"Total Time Taken for Marked Files Deletion = {mark_time} seconds")
    print(f"Total Auto Files Deleted = {tot_auto_delete}")
    print(f"Total Time Taken for Auto Deletion of Files {del_time - mark_time} seconds")
    print(f"Total No of Files Made Available = {avail_count}")
    print(f"Total No of Files Attached to Unavailable Documents = {non_avail_dt}")
    print(f"Total No of Files Not Available on Server = {non_avail_files}")
    print(f"Total Time Take for Making Files Available = {avail_time - mark_time} seconds")
    print(f"Total Time Taken {tot_time} seconds")


def check_correct_folders():
    log = frappe.logger("file_cleanup")
    start = time.time()

    rebuild_tree(doctype="File", parent_field="folder", group_field="is_folder")
    frappe.db.commit()

    rebuild_time = time.time()

    folders = frappe.db.sql("""
        SELECT name, folder, file_size, important_document_for_archive
        FROM `tabFile`
        WHERE is_folder = 1
    """, as_dict=True)

    # fetch folder sizes in one query
    folder_sizes = frappe.db.sql("""
        SELECT folder, SUM(file_size) AS size
        FROM `tabFile`
        WHERE folder IS NOT NULL
        GROUP BY folder
    """, as_dict=True)

    size_map = {f.folder: f.size for f in folder_sizes}

    # fetch parent flags once
    parent_flags = frappe.db.sql("""
        SELECT name, important_document_for_archive
        FROM `tabFile`
        WHERE is_folder = 1
    """, as_dict=True)

    parent_map = {p.name: p.important_document_for_archive for p in parent_flags}

    for fd in folders:

        fd_size = size_map.get(fd.name, 0)

        # inherit archive flag
        if fd.folder:
            parent_flag = parent_map.get(fd.folder)

            if parent_flag == 1 and fd.important_document_for_archive != 1:
                frappe.db.set_value(
                    "File",
                    fd.name,
                    "important_document_for_archive",
                    1
                )

        if fd_size != fd.file_size:
            frappe.db.set_value(
                "File",
                fd.name,
                "file_size",
                fd_size
            )

            log.info(
                f"Updating Folder {fd.name} size {fd.file_size} → {fd_size}"
            )

    log.info(f"Tree rebuild time = {int(rebuild_time - start)}s")
    log.info(f"Total runtime = {int(time.time() - start)}s")
