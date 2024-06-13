import logging

from auth_user_extrainfo.forms import ExtraInfoForm
from auth_user_extrainfo.models import ExtraInfo

from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.instructor_task.config.waffle import use_on_disk_grade_reporting
from lms.djangoapps.instructor_task.tasks_helper.grades import (
    CourseGradeReport,
    InMemoryReportMixin,
    TempFileCourseGradeReport,
    _CourseGradeBulkContext,
    _CourseGradeReportContext,
    _user_enrollment_status
)
from xmodule.modulestore.django import modulestore

logger = logging.getLogger(__name__)


class CustomCourseGradeReport(CourseGradeReport):

    @classmethod
    def generate(cls, _xblock_instance_args, _entry_id, course_id, _task_input, action_name):
        logger.warn("CustomCourseGradeReport is deprecated. Use CustomInMemoryCourseGradeReport or CustomTempFileCourseGradeReport instead.")
        """
        Public method to generate a grade report.
        """
        with modulestore().bulk_operations(course_id):
            context = _CourseGradeReportContext(_xblock_instance_args, _entry_id, course_id, _task_input, action_name)
            if use_on_disk_grade_reporting(course_id):  # AU-926
                return CustomTempFileCourseGradeReport(context)._generate()  # pylint: disable=protected-access
            else:
                return CustomInMemoryCourseGradeReport(context)._generate()  # pylint: disable=protected-access
    def _get_extra_info_model_attributes(self):
        # get all attributes from ExtraInfo model
        return list(ExtraInfo._meta.get_fields())
        
    def _success_headers(self):
        """
        Returns a list of all applicable column headers for this grade report.
        """
        return (
            ["Student ID", "Name" ,"Email", "Username","CPF"] +
            super()._grades_header() +
            (['Cohort Name'] if self.context.cohorts_enabled else []) +
            [f'Experiment Group ({partition.name})' for partition in self.context.course_experiments] +
            (['Team Name'] if self.context.teams_enabled else []) +
            ['Enrollment Track', 'Verification Status'] +
            ['Certificate Eligible', 'Certificate Delivered', 'Certificate Type'] +
            ['Enrollment Status']
        )
    def _rows_for_users(self, users):
        """
        Returns a list of rows for the given users for this report.
        """
        with modulestore().bulk_operations(self.context.course_id):
            bulk_context = _CourseGradeBulkContext(self.context, users)
            user_ids = [user.id for user in users]
            extra_info_data = ExtraInfo.objects.filter(user_id__in=user_ids).values_list('user_id', 'cpf')
            user_extra_info = {info[0]: info[1] for info in extra_info_data}
            success_rows, error_rows = [], []
            for user, course_grade, error in CourseGradeFactory().iter(
                users,
                course=self.context.course,
                collected_block_structure=self.context.course_structure,
                course_key=self.context.course_id,
            ):
                if not course_grade:
                    # An empty gradeset means we failed to grade a student.
                    error_rows.append([user.id, user.username, str(error)])
                else:
                    cpf = user_extra_info.get(user.id, "N/A")
                    full_name= user.first_name + " " + user.last_name
                    success_rows.append(
                        [user.id, full_name, user.email, user.username, cpf] +
                        self._user_grades(course_grade) +
                        self._user_cohort_group_names(user) +
                        self._user_experiment_group_names(user) +
                        self._user_team_names(user, bulk_context.teams) +
                        self._user_verification_mode(user, bulk_context.enrollments) +
                        self._user_certificate_info(user, course_grade, bulk_context.certs) +
                        [_user_enrollment_status(user, self.context.course_id)]
                    )
        return success_rows, error_rows
        
class CustomInMemoryCourseGradeReport(CustomCourseGradeReport, InMemoryReportMixin):
    def _generate(self):
        """
        Internal method for generating a grade report for the given context.
        """
        self.context.update_status('InMemoryReportMixin - 1: Starting grade report')
        success_headers = self._success_headers()
        error_headers = self._error_headers()
        batched_rows = self._batched_rows()

        self.context.update_status('InMemoryReportMixin - 2: Compiling grades')
        success_rows, error_rows = self._compile(batched_rows)

        self.context.update_status('InMemoryReportMixin - 3: Uploading grades')
        self._upload(success_headers, success_rows, error_headers, error_rows)

        return self.context.update_status('InMemoryReportMixin - 4: Completed grades')


class CustomTempFileCourseGradeReport(TempFileCourseGradeReport):
    """ Course Grade Report that compiles and then uploads all rows at once """