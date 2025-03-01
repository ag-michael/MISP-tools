import datetime
import logging
import concurrent.futures
from requests.exceptions import ConnectionError, SSLError
from .adversary import Adversary
from .report_type import ReportType
from .indicator_type import IndicatorType
from .actors import ActorsImporter
from .indicators import IndicatorsImporter
from .reports import ReportsImporter
from .threaded_misp import MISP
from .helper import (
    IMPORT_BANNER,
    DELETE_BANNER,
    INDICATOR_TYPES,
    display_banner,
    format_seconds,
    thousands
)


class CrowdstrikeToMISPImporter:
    """Tool used to import indicators and reports from the Crowdstrike Intel API.

    :param intel_api_client: client for the Crowdstrike Intel API
    :param import_settings: dictionary containing settings specified in settings.py
    :param provided_arguments: dictionary containing provided command line arguments
    """

    def __init__(self, intel_api_client, import_settings, provided_arguments, settings, logger: logging.Logger):
        """Construct an instance of the CrowdstrikeToMISPImporter class."""
        confirm_settings = ["misp_url", "misp_auth_key", "crowdstrike_org_uuid", "reports_timestamp_filename",
                            "indicators_timestamp_filename", "actors_timestamp_filename"
                            ]
        for item in confirm_settings:
            try:
                _ = import_settings[item]
            except KeyError as err:
                err_msg = ("%s value must be specified in the settings.py file."
                           " Please check your configuration and retry.\n%s",
                           item,
                           err
                           )
                logger.error(err_msg)
                raise SystemExit(err_msg) from err

        self.misp_client = MISP(import_settings["misp_url"],
                                import_settings["misp_auth_key"],
                                import_settings["misp_enable_ssl"],
                                False,
                                max_threads=import_settings["max_threads"],
                                logger=logger
                                )
        self.config = provided_arguments
        self.settings = settings
        # self.unique_tags = {
        #     "reports": import_settings["reports_unique_tag"],
        #     "indicators": import_settings["indicators_unique_tag"],
        #     "actors": import_settings["actors_unique_tag"],
        # }
        self.import_settings = import_settings
        self.log = logger
        self.event_ids = {}
        self.report_ids = {}
        self.actor_ids = {}
        self.indicator_ids = {}
        self.org_id = settings["MISP"]["crowdstrike_org_uuid"]

        if self.config["actors"]:
            self.actors_importer = ActorsImporter(self.misp_client,
                                                  intel_api_client,
                                                  import_settings["crowdstrike_org_uuid"],
                                                  import_settings["actors_timestamp_filename"],
                                                  self.settings,
                                                  self.import_settings,
                                                  logger=logger
                                                  )
        if self.config["reports"]:
            self.reports_importer = ReportsImporter(self.misp_client,
                                                    intel_api_client,
                                                    import_settings["crowdstrike_org_uuid"],
                                                    import_settings["reports_timestamp_filename"],
                                                    self.settings,
                                                    self.import_settings,
                                                    logger=logger
                                                    )
        if self.config["indicators"]:
            self.indicators_importer = IndicatorsImporter(self.misp_client, intel_api_client,
                                                          import_settings["crowdstrike_org_uuid"],
                                                          import_settings["indicators_timestamp_filename"],
                                                          self.config["indicators"],
                                                          self.config["delete_outdated_indicators"],
                                                          self.settings,
                                                          self.import_settings,
                                                          logger=logger
                                                          )


    def clean_crowdstrike_events(self, clean_reports, clean_indicators, clean_actors):
        """Delete events from a MISP instance."""
        # This search currently leverages `search_index` which searches event metadata displayed
        # on the event listing page. The tags used to identify the different event types CANNOT
        # be used for cross-tagging purposes at the event level (attributes are still fine).
        #   Adversaries: "CrowdStrike:adversary:branch: {ADVERSARY TYPE}"
        #   Indicators: "CrowdStrike:indicator:type: {INDICATOR TYPE}"
        #   Reports: "CrowdStrike:reports:type: {REPORT TYPE}"
        #
        # Passing in a list of tags to the search_index solution is pulling too many matches
        # back in environments with large numbers of events.
        def perform_threaded_delete(tag_to_hunt: str, tag_type: str, skip_tags: list = None, do_min: bool = False):
            if skip_tags == None:
                skip_tags = []
            self.log.info("Start clean up of CrowdStrike %s events from MISP.", tag_type)
            #qry = self.misp_client.build_complex_query(or_parameters=tag_to_hunt)
            params = {
                #"tags": [tag_to_hunt].extend(skip_tags)
                "tags": [tag_to_hunt],
                "org": self.org_id
            }
            if not self.import_settings["force"] and not do_min:
                params["minimal"] = True
            with concurrent.futures.ThreadPoolExecutor(self.misp_client.thread_count, thread_name_prefix="thread") as executor:
                #executor.map(self.misp_client.delete_event, self.misp_client.search_index(tags=tags, minimal=True))
                #executor.map(self.misp_client.delete_event, self.misp_client.search(tag=qry, minimal=True))  # seems to bog down
                executor.map(self.misp_client.delete_event, self.misp_client.search_index(**params))

        def perform_threaded_family_delete():
            self.log.info("Start clean up of CrowdStrike malware family indicator events from MISP.")
            with concurrent.futures.ThreadPoolExecutor(self.misp_client.thread_count, thread_name_prefix="thread") as executor:
                executor.map(self.misp_client.delete_event, self.misp_client.search(eventinfo="Malware Family:%"))
            

        display_banner(banner=DELETE_BANNER,
                       logger=self.log,
                       fallback="BEGIN DELETE",
                       hide_cool_banners=self.import_settings["no_banners"]
                       )
        if clean_actors:
            for adv_type in [a for a in dir(Adversary) if "__" not in a]:
                adv_time = datetime.datetime.now().timestamp()
                perform_threaded_delete(tag_to_hunt=f"CrowdStrike:adversary:branch: {adv_type}",
                                        tag_type=f"Adversary ({adv_type})",
                                        #skip_tags=get_feed_tags(do_not=True),
                                        do_min=True
                                        )
                adv_run_time = float(datetime.datetime.now().timestamp() - adv_time)
                self.log.info("Completed deletion of CrowdStrike %s adversaries within MISP in %s seconds",
                              adv_type,
                              format_seconds(adv_run_time)
                              )

        if clean_reports:
            for report_type in [r for r in dir(ReportType) if "__" not in r]:
                rep_time = datetime.datetime.now().timestamp()
                perform_threaded_delete(tag_to_hunt=f"CrowdStrike:report:type: {report_type}", tag_type=f"{report_type} report", do_min=True)
                rep_run_time = datetime.datetime.now().timestamp() - rep_time
                self.log.info("Completed deletion of CrowdStrike %s reports within MISP in %s seconds",
                              report_type,
                              format_seconds(rep_run_time)
                              )

        if clean_indicators:
            ind_time = datetime.datetime.now().timestamp()
            for ind_type in INDICATOR_TYPES:
                perform_threaded_delete(
                    tag_to_hunt=f"CrowdStrike:indicator:type: {ind_type.upper()}",
                    tag_type=f"{ind_type.upper()} indicator"
                    )
            for indy in [i for i in dir(IndicatorType) if "__" not in i]:
                perform_threaded_delete(
                    tag_to_hunt=f"CrowdStrike:indicator:feed:type: {indy}",
                    tag_type=f"{IndicatorType[indy].value} indicator type",
                    do_min=True
                )
            perform_threaded_family_delete()
            ind_run_time = datetime.datetime.now().timestamp() - ind_time
            self.log.info("Completed deletion of CrowdStrike indicators within MISP in %s seconds", format_seconds(ind_run_time))

        self.log.info("Finished cleaning up CrowdStrike related events from MISP, %i events deleted.", self.misp_client.deleted_event_count)
            
    def remove_crowdstrike_tags(self):
        """Remove all CrowdStrike local tags from the MISP instance."""
        display_banner(banner=DELETE_BANNER,
                       logger=self.log,
                       fallback="BEGIN DELETE",
                       hide_cool_banners=self.import_settings["no_banners"]
                       )
        # self.log.info(DELETE_BANNER)
        removed = 0
        self.log.info("Retrieving list of tags to remove from MISP instance")
        with concurrent.futures.ThreadPoolExecutor(self.misp_client.thread_count, thread_name_prefix="thread") as executor:
            futures = {
                executor.submit(self.misp_client.clear_tag, tg) for tg in self.misp_client.get_cs_tags()
            }
            for fut in concurrent.futures.as_completed(futures):
                removed = fut.result()
        self.log.info("Finished cleaning up CrowdStrike related tags from MISP, %i tags deleted.", removed)

    def clean_old_crowdstrike_events(self, max_age):
        """Remove events from MISP that are dated greater than the specified max_age value."""
        # TODO: Revisions required, this logic will no longer work as it is written.
        display_banner(banner=DELETE_BANNER,
                       logger=self.log,
                       fallback="BEGIN DELETE",
                       hide_cool_banners=self.import_settings["no_banners"]
                       )
        #self.log.info(DELETE_BANNER)
        if max_age is not None:
            timestamp_max = int((datetime.date.today() - datetime.timedelta(max_age)).strftime("%s"))
            events = self.misp_client.search(tags=["CrowdStrike:report%",
                                                   "CrowdStrike:indicator%",
                                                   "CrowdStrike:adversary%"
                                                   ],
                                             timestamp=[0, timestamp_max],
                                             org=self.org_id
                                             )
            with concurrent.futures.ThreadPoolExecutor(self.misp_client.thread_count, thread_name_prefix="thread") as executor:
                executor.map(self.misp_client.delete_event, events)
            self.log.info("Finished cleaning up CrowdStrike related events from MISP.")

    def import_from_crowdstrike(self,
                                reports_days_before: int = 1,
                                indicators_minutes_before: int = 1,
                                actors_days_before: int = 1
                                ):
        """Import reports and events from Crowdstrike Intel API.

        :param reports_days_before: in case on an initial run, this is the age of the reports pulled in days
        :param indicators_days_before: in case on an initial run, this is the age of the indicators pulled in days
        :param actors_days_before: in case on an initial run, this is the age of the actors pulled in days
        """
        display_banner(banner=IMPORT_BANNER,
                       logger=self.log,
                       fallback=None,
                       hide_cool_banners=self.import_settings["no_banners"]
                       )
        run_start_time = datetime.datetime.now().timestamp()
        if self.config["actors"]:
            import_start_time = datetime.datetime.now().timestamp()
            self.actors_importer.process_actors(actors_days_before, self.event_ids)
            actors_time = f"{datetime.datetime.now().timestamp() - import_start_time:.2f}"
            self.log.info("Completed import of adversaries into MISP in %s seconds", format_seconds(actors_time))
        if self.config["reports"]:
            import_start_time = datetime.datetime.now().timestamp()
            self.reports_importer.process_reports(reports_days_before, self.event_ids)
            reports_time = datetime.datetime.now().timestamp() - import_start_time
            self.log.info("Completed import of reports into MISP in %s seconds", format_seconds(reports_time))
        if self.config["indicators"]:
            #self.indicators_importer.process_indicators(indicators_minutes_before, self.event_ids, self.report_ids)
            import_start_time = datetime.datetime.now().timestamp()
            self.indicators_importer.process_indicators(indicators_minutes_before)
            indicators_time = datetime.datetime.now().timestamp() - import_start_time
            self.log.info("Completed import of indicators into MISP in %s seconds", format_seconds(indicators_time))

        total_run_time = datetime.datetime.now().timestamp() - run_start_time
        self.log.info("Import process completed in %s seconds", format_seconds(total_run_time))


    def attribute_search(self, att_name, att_type):
        """Search for indicators of a specific type and return a clean dictionary of the indicator and UUID."""
        clean_result = None
        try:
            result = self.misp_client.search(controller="attributes", type_attribute=att_type, include_event_uuid=True)
            clean_result = {
                res.get("value"): {
                    "event_uuid": res.get("event_uuid"),
                    "uuid": res.get("uuid")
                }
                for res in result.get("Attribute")
            }
            self.log.info("Retrieved %s %s indicators from MISP.", thousands(len(clean_result)), att_name)
        except (SSLError, ConnectionError):
            self.log.warning("Unable to retrieve %s attributes for duplicate checking.", att_type)

        return clean_result


    def threaded_report_search(self, evts, lock):
        returned = 0
        if evts.get("info"):
            with lock:
                self.event_ids[evts.get("info").split(" ")[0]] = evts["uuid"]
                self.report_ids[evts.get("info").split(" ")[0]] = {
                    "uuid": evts["uuid"],
                    "attributes": evts.get("attributes")
                }
            returned = len(self.report_ids[evts.get("info").split(" ")[0]].get("attributes", 0))

        return returned


    def import_from_misp(self, tags, style: str, do_reports: bool = False):
        """Retrieve existing MISP events."""
        events = self.misp_client.search_index(tags=tags)
        for event in events:
            if event.get('info'):
                if style == "actors":
                    self.actor_ids[event.get('info')] = event["uuid"]
                    self.event_ids[event.get('info')] = event["uuid"]
                elif style == "reports":
                    with concurrent.futures.ThreadPoolExecutor(self.misp_client.thread_count, thread_name_prefix="thread") as executor:
                        executor.map(self.threaded_report_search, )
                    self.event_ids[event.get("info").split(" ")[0]] = event["uuid"]
                    self.report_ids[event.get("info").split(" ")[0]] = {
                        "uuid": event["uuid"],
                #        "attributes": self.misp_client.search(controller="attributes", uuid=event["uuid"])
                    }

                elif style == "indicators":
                    self.event_ids[event.get('info')] = event["uuid"]
                    self.indicator_ids[event.get('info')] = event["uuid"]
            else:
                self.log.warning("Event %s missing info field.", event)
