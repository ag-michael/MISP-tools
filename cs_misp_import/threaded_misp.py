import logging
import requests
import time
import os

from .misp_safe_check_response import safe_check_response
try:
    import pymisp
    pymisp.api.everything_broken = {"key": ""}
    pymisp.api.PyMISP._check_response = safe_check_response

    from pymisp import ExpandedPyMISP, PyMISPError

except ImportError as no_pymisp:
    raise SystemExit(
        "The PyMISP project must be installed in order to use this program."
        ) from no_pymisp


class MISP(ExpandedPyMISP):
    MAX_RETRIES = 3

    def __init__(self, *args, **kwargs):
        self.thread_count = int(kwargs.get("max_threads") or min(32, (os.cpu_count() or 1) * 4))
        self.log: logging.Logger = kwargs.get("logger", None)
        kwargs.pop("logger")
        kwargs.pop("max_threads")
        super().__init__(*args, **kwargs)
        self._PyMISP__session.mount('https://', requests.adapters.HTTPAdapter(pool_connections=int(self.thread_count), pool_maxsize=int(self.thread_count)*2))
        self.deleted_attribute_count = 0        
        self.deleted_event_count = 0
        self.deleted_tag_count = 0
        self.added_sighting_count = 0
        self.updated_event_count = 0


    def delete_event(self, *args, **kwargs):
        if self.deleted_event_count % 50 == 0 and self.deleted_event_count:
            self.log.info("%i events deleted", self.deleted_event_count, extra={"key": ""})
        result = self._retry(super().delete_event, *args, **kwargs)
        if "errors" not in result:
            self.deleted_event_count += 1
    

    def add_sighting(self, *args, **kwargs):
        thread_lock = kwargs.get("lock", None)
        if thread_lock:
            kwargs.pop("lock")
        result = self._retry(super().add_sighting, *args, **kwargs)
        if "errors" not in result:
            if thread_lock:
                with thread_lock:
                    if self.added_sighting_count % 50 == 0 and self.added_sighting_count:
                        self.log.info("%i sightings added", self.added_sighting_count)
                    self.added_sighting_count += 1
            else:
                if self.added_sighting_count % 50 == 0 and self.added_sighting_count:
                    self.log.info("%i sightings added", self.added_sighting_count)
                self.added_sighting_count += 1


    #def update_event(self, *args, **kwargs):
        # thread_lock = kwargs.get("lock", None)
        # kwargs.pop("lock")
        # if self.updated_event_count % 50 == 0 and self.updated_event_count:
        #     self.log.info("%i events updated", self.updated_event_count)
        # result = self._retry(super().update_event, *args, **kwargs)
        #self._retry(super().update_event, *args, **kwargs)
        # if "errors" not in result:
        #     if thread_lock:
        #         with thread_lock:
        #             self.updated_event_count += 1
        #     else:
        #         self.updated_event_count += 1

    def delete_attribute(self, *args, **kwargs):
        if self.deleted_attribute_count % 50 == 0 and self.deleted_attribute_count:
            self.log.info("%i attributes deleted", self.deleted_attribute_count, extra={"key": ""})
        result = self._retry(super().delete_attribute, *args, **kwargs)
        if result:
            if "errors" not in result:
                self.deleted_attribute_count += 1

    def get_cs_tags(self):
        return self.search_tags("CrowdStrike:%")
        
    def clear_tag(self, *args, **kwargs):
#        tags = self.search_tags("CrowdStrike:%")
        #for tag in kwargstags:
        tag = args[0]
        if tag:
            if not self.deleted_tag_count % 50 and self.deleted_tag_count:
                self.log.info("%i tags deleted", self.deleted_tag_count, extra={"key": ""})
            result = self._retry(self.delete_tag, tag, **kwargs)
            if "errors" not in result:
                self.deleted_tag_count += 1

        return self.deleted_tag_count
        #self.log.info("%i tags deleted", self.deleted_tag_count)

    def get_adversaries(self, *args, **kwargs):
        adv = self.search(info="ADV-%")
        return adv

    def get_organisation(self, *args, **kwargs):
        return self._retry(super().get_organisation, *args, **kwargs)

    def _retry(self, f, *args, **kwargs):
        for i in range(self.MAX_RETRIES):
            try:
                response = f(*args, **kwargs)

                if "errors" not in response:
                    return response
                if response["errors"][0] == 404:
                    return response

                if i + 1 < self.MAX_RETRIES:
                    timeout = 0.3 * 2 ** i
                    #self.log.warning('Caught an error from the MISP server. (T⌓T)', extra={"key": ""})
                    self.log.warning('%s', dict(response['errors'])["message"], extra={"key": ""})
                    self.log.warning('Retrying request in %.2f seconds. (T⌓T)', timeout, extra={"key": ""})
                    time.sleep(timeout)
                else:
                    raise PyMISPError("MISP Error: {}".format(response['errors']))
            except Exception as e:
                if i + 1 < self.MAX_RETRIES:
                    timeout = 0.3 * 2 ** i
                    #self.log.warning('Caught an error from the MISP server. ¯\_(ツ)_/¯', extra={"key": ""})
                    self.log.warning('%s', str(e), extra={"key": ""})
                    self.log.warning('Retrying request in %.2f seconds. ¯\_(ツ)_/¯', timeout, extra={"key": ""})
                    time.sleep(timeout)
                else:
                    self.log.error('Unresolvable error received from the MISP server.', extra={"key": ""})
                    try:
                        self.log.error('%s', str(e.message), extra={"key": ""})
                    except Exception as _:
                        self.log.error("%s", str(e), extra={"key": ""})
                    #self.log.exception("%s", dict(response['errors'])["message"], extra={"key": ""})
                    self.log.error("Exceeded number of retries. (╯°□°）╯︵ ┻━┻", extra={"key": ""})
                    #raise e


