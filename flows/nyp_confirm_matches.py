"""nyp_confirm_matches — a form where a person ticks the bank matches to confirm.

Closer calls this agentic workflow instead of asking the admin to type
"line 27 is 1021". The form shows one row per bank line and candidate order
(from nyp_match_options); the ticked rows come back as `selected`, which
Closer adds to approved_matches. The tools still check every match.

Import with:  orchestrate tools import -k flow -f flows/nyp_confirm_matches.py
(after the nyp_ python tools, so nyp_match_options exists).
"""

from pydantic import BaseModel, Field

from ibm_watsonx_orchestrate.flow_builder.flows import END, START, Flow, flow

from ibm_watsonx_orchestrate.flow_builder.data_map import Assignment, DataMap


class MonthInput(BaseModel):
    month: str = Field(description="ISO year-month of the close")


class MatchRow(BaseModel):
    line_no: str
    description: str
    amount: str
    order_id: str
    salesperson: str
    label: str


class MatchOptions(BaseModel):
    options: list[MatchRow]


class Selected(BaseModel):
    selected: list[MatchRow] = Field(description="The rows the person ticked")


@flow(
    name="nyp_confirm_matches",
    display_name="Confirm bank matches",
    description=(
        "Show the admin a form listing each unmatched bank credit with its candidate "
        "orders, and return the rows they tick. Call it when bank credits need a "
        "person to confirm them."
    ),
    input_schema=MonthInput,
    output_schema=Selected,
)
def build_nyp_confirm_matches(aflow: Flow) -> Flow:
    options = aflow.tool(
        "nyp_match_options",
        name="get_options",
        input_schema=MonthInput,
        output_schema=MatchOptions,
    )
    options.map_input("month", "flow.input.month")

    ask = aflow.userflow(name="ask_admin", label="Confirm bank matches for {flow.input.month}")
    form = ask.form(
        name="confirm",
        display_name="Confirm bank matches",
        instructions="Tick each bank credit whose order is correct. Leave a row unticked if unsure.",
        submit_button_label="Confirm ticked matches",
    )
    choices = DataMap()
    choices.add(Assignment(target_variable="self.input.choices",
                           value_expression="flow.get_options.output.options"))
    form.multi_choice_input_field(
        name="matches",
        label="Bank credits and candidate orders",
        required=True,
        choices=choices,
        show_as_dropdown=False,
        columns={
            "line_no": "Bank line",
            "description": "Description",
            "amount": "Amount",
            "order_id": "Order",
            "salesperson": "Salesperson",
        },
    )
    ask.sequence(START, form, END)

    aflow.sequence(START, options, ask, END)
    # At runtime, form values are read by display name and field label, not
    # by internal name (see the ADK's masking_test_flow example).
    aflow.map_output(
        "selected",
        'flow["ask_admin"]["Confirm bank matches"].output["Bank credits and candidate orders"]',
    )
    return aflow
