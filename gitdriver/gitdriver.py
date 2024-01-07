#!/usr/bin/python

import logging
import os
import pprint
import sys
import argparse
import mimetypes
import subprocess
import yaml
import pandoc
import datetime
import pytz

from . import pandoc_converter
from .drive import GoogleDrive, DRIVE_RW_SCOPE

# override text/plain as otherwise it returns .ksh
MIME_TYPE_SUFFIXES = {
    'application/x-vnd.oasis.opendocument.spreadsheet': '.ods',
    'application/epub+zip': '.epub',
    'text/plain': '.txt',
}
EASTERN = pytz.timezone("US/Eastern")

def commit_revision(gd, opts, rev, md, target_dir=None, type_suffix=''):
    # Prepare environment variables to change commit time
    env = os.environ.copy()
    date = rev['modifiedTime']
    newdate = pytz.utc.localize(datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%fZ"))  # 2023-08-09T03:38:09.920Z
    convdate = datetime.datetime.strftime(newdate.astimezone(EASTERN), "%-m/%-d/%y @ %-I:%M:%S %p")  # 8/8/23 @ 11:38:09 PM
    basename = md.get('name', 'content').replace('/', '_') + type_suffix
    user = rev.get('lastModifyingUser', {}).get('displayName', None)
    email = rev.get('lastModifyingUser', {}).get('emailAddress', None)
    if (user is None) or (email is None):
        logging.warning("Could not determine user from revision info:\n%s", pprint.pformat(rev))
    env['GIT_COMMITTER_DATE'] = env['GIT_AUTHOR_DATE'] = date
    env['GIT_COMMITTER_NAME'] = env['GIT_AUTHOR_NAME'] = user or 'Unknown User'
    env['GIT_COMMITTER_EMAIL'] = env['GIT_AUTHOR_EMAIL'] = email or 'unknown'

    mime_types = rev['exportLinks'].keys() if opts.all_types else opts.mime_types
    for mime_type in mime_types:
        if mime_type in opts.exclude_types:
            continue
        filename_suffix = MIME_TYPE_SUFFIXES.get(mime_type, mimetypes.guess_extension(mime_type, False))
        if not filename_suffix:
            logging.warning("Could not determine extension for mime_type %s", mime_type)
            filename_suffix = ".%s" % mime_type.replace('/', '_')
        filename = '%s%s' % (basename, filename_suffix)
        if target_dir is not None:
            filename = os.path.join(target_dir, filename)
        with open(filename, 'wb') as fd:
            if 'exportLinks' in rev and not opts.raw:
                # If the file provides an 'exportLinks' dictionary,
                # download the requested MIME type.
                r = gd.session.get(rev['exportLinks'][mime_type])
            elif 'downloadUrl' in rev:
                # Otherwise, if there is a downloadUrl, use that.
                r = gd.session.get(rev['downloadUrl'])
            else:
                raise KeyError('unable to download revision')

            # Write file content into local file.
            for chunk in r.iter_content():
                fd.write(chunk)

        # add changes to repository.
        if not opts.markdown:
            subprocess.call(['git', 'add', filename])
    # check if it needs to be converted
    if opts.markdown:
        # convert the file
        converted_filename = pandoc_converter.convert(filename, 'md')
        # add changes to repository.
        subprocess.call(['git', 'add', converted_filename])
    # committing the changes
    subprocess.call(['git', 'commit', '-m', 'Revision from %s' % convdate], env=env)





def main(opts):
    if not opts.mime_types and not opts.all_types:
        print("At least one mime-type must be given! Alternatively, use one between -T, -H or --all-types")
        exit(1)
    cfg = yaml.safe_load(open(opts.config))
    gd = GoogleDrive(
            client_id=cfg['googledrive']['client id'],
            client_secret=cfg['googledrive']['client secret'],
            scopes=[DRIVE_RW_SCOPE],
            )

    # Establish our credentials.
    gd.authenticate()

    # Get information about the specified file.  This will throw
    # an exception if the file does not exist.
    md = gd.get_file_metadata(opts.docid)

    doc_title = md['name']
    if os.path.isdir(doc_title):
        # Find revision matching last commit and process only following revisions
        os.chdir(doc_title)
        print('Update repository "%s"' % doc_title)
        last_commit_message = subprocess.check_output('git log -n 1 --format=%B', shell=True, encoding='utf-8')
        print('Last commit: ' + last_commit_message + 'Iterating Google Drive revisions:')
        revision_matched = False
        for rev in gd.revisions(opts.docid):
            if revision_matched:
                print("New revision: " + rev['modifiedTime'])
                commit_revision(gd, opts, rev, md)
            date = rev['modifiedTime']
            newdate = pytz.utc.localize(datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%fZ"))  # 2023-08-09T03:38:09.920Z
            convdate = datetime.datetime.strftime(newdate.astimezone(EASTERN), "%-m/%-d/%y @ %-I:%M:%S %p")  # 8/8/23 @ 11:38:09 PM
            if convdate in last_commit_message:
                print("Found matching revision: " + rev['modifiedTime'])
                revision_matched = True
        print("Repository is up to date.")
    else:
        # Initialize the git repository.
        print('Create repository "%s"' % doc_title)
        subprocess.call(['git','init',doc_title])
        os.chdir(doc_title)

        # Iterate over the revisions (from oldest to newest).
        for rev in gd.revisions(opts.docid):
            commit_revision(gd, opts, rev, md)


