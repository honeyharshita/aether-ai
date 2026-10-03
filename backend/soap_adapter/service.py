"""
Isolated SOAP adapter (spec Section 7).

REST is the primary API style for AetherAI (see app/routers/*). This
module is a deliberately small, isolated SOAP 1.1 service that exposes
ONE read-only operation -- GetProjectHealth -- purely to give a real,
working REST-vs-SOAP comparison point, as the spec requests. It is not
imported by app/main.py and does not participate in the main
architecture; run it separately (a different process/port) if you want
to exercise it.

Run: uvicorn soap_adapter.service:soap_app --port 8001
Then: curl -X POST http://localhost:8001/soap/project-health \
        -H "Content-Type: text/xml" --data-binary @sample_request.xml
"""
import os
import sys
from xml.sax.saxutils import escape
from fastapi import FastAPI, Request, Response

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.routers.projects import project_health as rest_project_health  # noqa: E402

soap_app = FastAPI(title="AetherAI SOAP Adapter (isolated demo)")

WSDL = """<?xml version="1.0"?>
<definitions name="AetherAIProjectHealth"
  targetNamespace="urn:aetherai:projecthealth"
  xmlns="http://schemas.xmlsoap.org/wsdl/"
  xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
  xmlns:tns="urn:aetherai:projecthealth"
  xmlns:xsd="http://www.w3.org/2001/XMLSchema">

  <message name="GetProjectHealthRequest">
    <part name="project_id" type="xsd:string"/>
  </message>
  <message name="GetProjectHealthResponse">
    <part name="health_score" type="xsd:decimal"/>
    <part name="status" type="xsd:string"/>
  </message>

  <portType name="ProjectHealthPortType">
    <operation name="GetProjectHealth">
      <input message="tns:GetProjectHealthRequest"/>
      <output message="tns:GetProjectHealthResponse"/>
    </operation>
  </portType>

  <binding name="ProjectHealthBinding" type="tns:ProjectHealthPortType">
    <soap:binding style="rpc" transport="http://schemas.xmlsoap.org/soap/http"/>
    <operation name="GetProjectHealth">
      <soap:operation soapAction="urn:aetherai:projecthealth#GetProjectHealth"/>
      <input><soap:body use="encoded" namespace="urn:aetherai:projecthealth"
        encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"/></input>
      <output><soap:body use="encoded" namespace="urn:aetherai:projecthealth"
        encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"/></output>
    </operation>
  </binding>

  <service name="ProjectHealthService">
    <port name="ProjectHealthPort" binding="tns:ProjectHealthBinding">
      <soap:address location="http://localhost:8001/soap/project-health"/>
    </port>
  </service>
</definitions>
"""


@soap_app.get("/soap/project-health.wsdl")
def wsdl():
    return Response(content=WSDL, media_type="text/xml")


def _extract_project_id(xml_body: str) -> str:
    # Minimal, dependency-free extraction for this single demo operation
    # (a production SOAP service would use a real XML/WSDL toolkit such
    # as spyne or zeep-server; kept minimal here to stay isolated).
    import re
    m = re.search(r"<project_id>(.*?)</project_id>", xml_body)
    if not m:
        raise ValueError("project_id element not found in SOAP body")
    return m.group(1)


@soap_app.post("/soap/project-health")
async def get_project_health_soap(request: Request):
    body = (await request.body()).decode("utf-8")
    try:
        project_id = _extract_project_id(body)
    except ValueError as e:
        fault = f"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body><soap:Fault>
    <faultcode>soap:Client</faultcode>
    <faultstring>{escape(str(e))}</faultstring>
  </soap:Fault></soap:Body>
</soap:Envelope>"""
        return Response(content=fault, media_type="text/xml", status_code=400)

    # Reuse the SAME real health computation the REST API uses -- this
    # SOAP endpoint is a transport/format adapter, not a second
    # implementation of business logic.
    db = SessionLocal()
    try:
        result = rest_project_health(project_id, user=None, db=db)  # noqa: this bypasses auth for the isolated demo only
    finally:
        db.close()

    envelope = f"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetProjectHealthResponse xmlns="urn:aetherai:projecthealth">
      <health_score>{escape(str(result.get("health_score")))}</health_score>
      <status>ok</status>
    </GetProjectHealthResponse>
  </soap:Body>
</soap:Envelope>"""
    return Response(content=envelope, media_type="text/xml")
