#!/usr/bin/env python3

###############################################################
# Copyright 2020 Lawrence Livermore National Security, LLC
# (c.f. AUTHORS, NOTICE.LLNS, COPYING)
#
# This file is part of the Flux resource manager framework.
# For details, see https://github.com/flux-framework.
#
# SPDX-License-Identifier: LGPL-3.0
###############################################################
import unittest
import os
import sqlite3
import time

import pandas as pd

from unittest import mock

from flux.accounting import job_archive_interface as jobs
from flux.accounting import create_db as c
from flux.accounting import accounting_cli_functions as aclif


class TestAccountingCLI(unittest.TestCase):
    # create accounting, job-archive databases
    @classmethod
    def setUpClass(self):
        global jobs_conn

        # create example job-archive database, output file
        global op
        op = "job_records.csv"

        jobs_conn = sqlite3.connect("file:jobs.db?mode:rwc", uri=True)
        jobs_conn.execute(
            """
                CREATE TABLE IF NOT EXISTS jobs (
                    id            int       NOT NULL,
                    userid        int       NOT NULL,
                    username      text      NOT NULL,
                    ranks         text      NOT NULL,
                    t_submit      real      NOT NULL,
                    t_sched       real      NOT NULL,
                    t_run         real      NOT NULL,
                    t_cleanup     real      NOT NULL,
                    t_inactive    real      NOT NULL,
                    eventlog      text      NOT NULL,
                    jobspec       text      NOT NULL,
                    R             text      NOT NULL,
                    PRIMARY KEY   (id)
            );"""
        )

        c.create_db("FluxAccountingUsers.db")
        global acct_conn
        acct_conn = sqlite3.connect("FluxAccountingUsers.db")

        # add bank hierarchy
        aclif.add_bank(acct_conn, bank="A", shares=1)
        aclif.add_bank(acct_conn, bank="B", parent_bank="A", shares=1)
        aclif.add_bank(acct_conn, bank="C", parent_bank="B", shares=1)
        aclif.add_bank(acct_conn, bank="D", parent_bank="B", shares=1)

        # add users
        aclif.add_user(acct_conn, username="1001", bank="C")
        aclif.add_user(acct_conn, username="1002", bank="C")
        aclif.add_user(acct_conn, username="1003", bank="D")
        aclif.add_user(acct_conn, username="1004", bank="D")

        jobid = 100
        interval = 0  # add to job timestamps to diversify job-archive records

        @mock.patch("time.time", mock.MagicMock(return_value=10000000))
        def populate_job_archive_db(jobs_conn, userid, username, ranks, num_entries):
            nonlocal jobid
            nonlocal interval
            t_inactive_delta = 2000

            for i in range(num_entries):
                try:
                    jobs_conn.execute(
                        """
                        INSERT INTO jobs (
                            id,
                            userid,
                            username,
                            ranks,
                            t_submit,
                            t_sched,
                            t_run,
                            t_cleanup,
                            t_inactive,
                            eventlog,
                            jobspec,
                            R
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            jobid,
                            userid,
                            username,
                            ranks,
                            (time.time() + interval) - 2000,
                            (time.time() + interval) - 1000,
                            (time.time() + interval),
                            (time.time() + interval) + 1000,
                            (time.time() + interval) + t_inactive_delta,
                            "eventlog",
                            "jobspec",
                            '{"version":1,"execution": {"R_lite":[{"rank":"0","children": {"core": "0"}}]}}',
                        ),
                    )
                    # commit changes
                    jobs_conn.commit()
                # make sure entry is unique
                except sqlite3.IntegrityError as integrity_error:
                    print(integrity_error)

                jobid += 1
                interval += 10000
                t_inactive_delta += 100

        # populate the job-archive DB with fake job entries
        populate_job_archive_db(jobs_conn, 1001, "1001", "0", 2)

        populate_job_archive_db(jobs_conn, 1002, "1002", "0-1", 3)
        populate_job_archive_db(jobs_conn, 1002, "1002", "0", 2)

        populate_job_archive_db(jobs_conn, 1003, "1003", "0-2", 3)

        populate_job_archive_db(jobs_conn, 1004, "1004", "0-3", 4)
        populate_job_archive_db(jobs_conn, 1004, "1004", "0", 4)

    # passing a valid jobid should return
    # its job information
    def test_01_with_jobid_valid(self):
        my_dict = {"jobid": 102}
        job_records = jobs.view_job_records(jobs_conn, op, **my_dict)
        self.assertEqual(len(job_records), 1)

    # passing a bad jobid should return a
    # failure message
    def test_02_with_jobid_failure(self):
        my_dict = {"jobid": 000}
        job_records = jobs.view_job_records(jobs_conn, op, **my_dict)
        self.assertEqual(len(job_records), 0)

    # passing a timestamp before the first job to
    # start should return all of the jobs
    def test_03_after_start_time_all(self):
        my_dict = {"after_start_time": 0}
        job_records = jobs.view_job_records(jobs_conn, op, **my_dict)
        self.assertEqual(len(job_records), 18)

    # passing a timestamp after all of the start time
    # of all the completed jobs should return a failure message
    @mock.patch("time.time", mock.MagicMock(return_value=11000000))
    def test_04_after_start_time_none(self):
        my_dict = {"after_start_time": time.time()}
        job_records = jobs.view_job_records(jobs_conn, op, **my_dict)
        self.assertEqual(len(job_records), 0)

    # passing a timestamp before the end time of the
    # last job should return all of the jobs
    @mock.patch("time.time", mock.MagicMock(return_value=11000000))
    def test_05_before_end_time_all(self):
        my_dict = {"before_end_time": time.time()}
        job_records = jobs.view_job_records(jobs_conn, op, **my_dict)
        self.assertEqual(len(job_records), 18)

    # passing a timestamp before the end time of
    # the first completed jobs should return no jobs
    def test_06_before_end_time_none(self):
        my_dict = {"before_end_time": 0}
        job_records = jobs.view_job_records(jobs_conn, op, **my_dict)
        self.assertEqual(len(job_records), 0)

    # passing a user not in the jobs table
    # should return no jobs
    def test_07_by_user_failure(self):
        my_dict = {"user": "9999"}
        job_records = jobs.view_job_records(jobs_conn, op, **my_dict)
        self.assertEqual(len(job_records), 0)

    # view_jobs_run_by_username() interacts with a
    # passwd file; for the purpose of these tests,
    # just pass the userid
    def test_08_by_user_success(self):
        my_dict = {"user": "1001"}
        job_records = jobs.view_job_records(jobs_conn, op, **my_dict)
        self.assertEqual(len(job_records), 2)

    # passing a combination of params should further
    # refine the query
    @mock.patch("time.time", mock.MagicMock(return_value=10000500))
    def test_09_multiple_params(self):
        my_dict = {"user": "1001", "after_start_time": time.time()}
        job_records = jobs.view_job_records(jobs_conn, "records.csv", **my_dict)
        self.assertEqual(len(job_records), 1)

    # passing no parameters will result in a generic query
    # returning all results
    def test_10_no_options_passed(self):
        my_dict = {}
        job_records = jobs.view_job_records(jobs_conn, op, **my_dict)
        self.assertEqual(len(job_records), 18)

    # users that have run a lot of jobs should have a larger usage factor
    def test_11_calc_usage_factor_many_jobs(self):
        user = "1002"
        bank = "C"
        update_stmt = "UPDATE job_usage_factor_table SET usage_factor_period_0=256 WHERE username='1002' AND bank='C'"
        acct_conn.execute(update_stmt)
        update_stmt = "UPDATE job_usage_factor_table SET usage_factor_period_1=64 WHERE username='1002' AND bank='C'"
        acct_conn.execute(update_stmt)
        update_stmt = "UPDATE job_usage_factor_table SET usage_factor_period_2=16 WHERE username='1002' AND bank='C'"
        acct_conn.execute(update_stmt)
        update_stmt = "UPDATE job_usage_factor_table SET usage_factor_period_3=8 WHERE username='1002' AND bank='C'"
        acct_conn.execute(update_stmt)
        acct_conn.commit()

        usage_factor = jobs.calc_usage_factor(
            jobs_conn, acct_conn, priority_decay_half_life=1, user=user, bank=bank
        )
        self.assertEqual(usage_factor, 17044.0)

    # on the contrary, users that have not run a lot of jobs should have
    # a smaller usage factor
    def test_12_calc_usage_factor_few_jobs(self):
        user = "1001"
        bank = "C"
        update_stmt = "UPDATE job_usage_factor_table SET usage_factor_period_0=4096 WHERE username='1001' AND bank='C'"
        acct_conn.execute(update_stmt)
        update_stmt = "UPDATE job_usage_factor_table SET usage_factor_period_1=256 WHERE username='1001' AND bank='C'"
        acct_conn.execute(update_stmt)
        update_stmt = "UPDATE job_usage_factor_table SET usage_factor_period_2=32 WHERE username='1001' AND bank='C'"
        acct_conn.execute(update_stmt)
        update_stmt = "UPDATE job_usage_factor_table SET usage_factor_period_3=16 WHERE username='1001' AND bank='C'"
        acct_conn.execute(update_stmt)
        acct_conn.commit()

        usage_factor = jobs.calc_usage_factor(
            jobs_conn, acct_conn, priority_decay_half_life=1, user=user, bank=bank
        )
        self.assertEqual(usage_factor, 8500.0)

    # make sure usage factor was written to job_usage_factor_table
    # and association_table
    def test_13_check_usage_factor_in_table(self):
        select_stmt = "SELECT usage_factor_period_0 FROM job_usage_factor_table WHERE username='1002' AND bank='C'"
        dataframe = pd.read_sql_query(select_stmt, acct_conn)
        usage_factor = dataframe.iloc[0]
        self.assertEqual(usage_factor["usage_factor_period_0"], 17044.0)

        select_stmt = (
            "SELECT job_usage FROM association_table WHERE username='1002' AND bank='C'"
        )
        dataframe = pd.read_sql_query(select_stmt, acct_conn)
        job_usage = dataframe.iloc[0]
        self.assertEqual(job_usage["job_usage"], 17044.0)

    # re-calculating a job usage factor after the end of the last half-life
    # period should create a new usage bin and update t_half_life_period_table
    # with the new end time of the current half-life period
    def test_14_append_jobs_in_diff_half_life_period(self):
        user = "1001"
        bank = "C"

        try:
            jobs_conn.execute(
                """
                INSERT INTO jobs (
                    id,
                    userid,
                    username,
                    ranks,
                    t_submit,
                    t_sched,
                    t_run,
                    t_cleanup,
                    t_inactive,
                    eventlog,
                    jobspec,
                    R
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "200",
                    "1001",
                    "1001",
                    "0",
                    time.time() + 11604800,
                    time.time() + 11604900,
                    time.time() + 11605000,
                    time.time() + 11605100,
                    time.time() + 11605200,
                    "eventlog",
                    "jobspec",
                    '{"version":1,"execution": {"R_lite":[{"rank":"0","children": {"core": "0"}}]}}',
                ),
            )
            # commit changes
            jobs_conn.commit()
        # make sure entry is unique
        except sqlite3.IntegrityError as integrity_error:
            print(integrity_error)

        # re-calculate usage factor for user1001
        usage_factor = jobs.calc_usage_factor(
            jobs_conn, acct_conn, priority_decay_half_life=1, user=user, bank=bank
        )
        self.assertEqual(usage_factor, 4519.0)

    # simulate a half-life period further; re-calculate
    # usage for user1001 to make sure its value goes down
    @mock.patch(
        "time.time", mock.MagicMock(return_value=(time.time() + (604800 * 2.1)))
    )
    def test_15_recalculate_usage_after_half_life_period(self):
        user = "1001"
        bank = "C"

        usage_factor = jobs.calc_usage_factor(
            jobs_conn, acct_conn, priority_decay_half_life=1, user=user, bank=bank
        )

        self.assertEqual(usage_factor, 2277.00)

    # simulate a half-life period further; assure the new end of the
    # current half-life period gets updated
    @mock.patch(
        "time.time", mock.MagicMock(return_value=(time.time() + (604800 * 2.1)))
    )
    def test_16_update_end_half_life_period(self):
        # fetch timestamp of the end of the current half-life period
        fetch_half_life_timestamp_query = """
            SELECT end_half_life_period
            FROM t_half_life_period_table
            WHERE cluster='cluster'
            """
        dataframe = pd.read_sql_query(fetch_half_life_timestamp_query, acct_conn)
        old_end_half_life = dataframe.iloc[0]

        jobs.update_end_half_life_period(acct_conn, priority_decay_half_life=1)

        # fetch timestamp of the end of the new half-life period
        fetch_half_life_timestamp_query = """
            SELECT end_half_life_period
            FROM t_half_life_period_table
            WHERE cluster='cluster'
            """
        dataframe = pd.read_sql_query(fetch_half_life_timestamp_query, acct_conn)
        new_end_half_life = dataframe.iloc[0]

        self.assertGreater(float(new_end_half_life), float(old_end_half_life))

    # removing a user from the flux-accounting DB should NOT remove their job
    # usage history from the job_usage_factor_table
    def test_17_keep_job_usage_records_upon_delete(self):
        aclif.delete_user(acct_conn, username="1001", bank="C")

        select_stmt = """
            SELECT * FROM
            job_usage_factor_table
            WHERE username='1001'
            AND bank='C'
            """

        dataframe = pd.read_sql_query(select_stmt, acct_conn)
        self.assertEqual(len(dataframe), 1)

    # remove database and log file
    @classmethod
    def tearDownClass(self):
        jobs_conn.close()
        os.remove("jobs.db")
        os.remove("job_records.csv")
        os.remove("FluxAccountingUsers.db")


def suite():
    suite = unittest.TestSuite()

    return suite


if __name__ == "__main__":
    from pycotap import TAPTestRunner

    unittest.main(testRunner=TAPTestRunner())
