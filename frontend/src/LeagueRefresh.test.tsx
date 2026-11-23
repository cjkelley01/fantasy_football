import {render,screen,waitFor} from "@testing-library/react";
import {LeagueRefresh} from "./LeagueRefresh";
import {api} from "./lib/api";
import {snapshot} from "./test/fixtures";
import type {SnapshotRefreshRecord} from "./types/api";

const profile={profile_id:"configured_local_league" as const,league_id:42,season:2026,credential_mode:"public" as const,manual_rule_configuration_available:false,automatic_refresh_enabled:true};
afterEach(()=>vi.restoreAllMocks());
test("startup refresh requires persistent profile opt-in",async()=>{
 const discover=vi.spyOn(api,"refreshProfile").mockResolvedValue({...profile,automatic_refresh_enabled:false});
 vi.spyOn(api,"currentRefresh").mockResolvedValue(null);
 const submit=vi.spyOn(api,"submitRefresh");
 render(<LeagueRefresh open={false} automaticStart onClose={()=>{}} onPublished={()=>{}}/>);
 await waitFor(()=>expect(discover).toHaveBeenCalledOnce());
 await new Promise(resolve=>setTimeout(resolve,0));
 expect(submit).not.toHaveBeenCalled();
});
test("authorized startup sends no week override and submits once across view changes",async()=>{
 vi.spyOn(api,"refreshProfile").mockResolvedValue(profile);
 vi.spyOn(api,"currentRefresh").mockResolvedValue(null);
 const submit=vi.spyOn(api,"submitRefresh").mockResolvedValue({api_schema_version:"1.0",refresh_id:"auto",status:"queued",created_at_utc:"2026-11-20T12:00:00Z"});
 vi.spyOn(api,"refresh").mockImplementation(()=>new Promise(()=>{}));
 const props={automaticStart:true,onClose:()=>{},onPublished:()=>{}};
 const {rerender}=render(<LeagueRefresh {...props} open={false}/>);
 await waitFor(()=>expect(submit).toHaveBeenCalledOnce());
 expect(submit.mock.calls[0][0]).toMatchObject({startup_refresh:true,first_simulation_week:null});
 rerender(<LeagueRefresh {...props} open/>);
 await new Promise(resolve=>setTimeout(resolve,0));
 expect(submit).toHaveBeenCalledOnce();
});

test.each(["completed", "failed"] as const)("reopening with startup observation discovers a newer refresh that ends %s",async(status)=>{
 vi.spyOn(api,"refreshProfile").mockResolvedValue(profile);
 const original:SnapshotRefreshRecord={
  api_schema_version:"1.0",refresh_id:"original",league_id:42,season:2026,status:"running",
  stage:"retrieving_and_validating",created_at_utc:"2026-11-20T12:00:00Z",started_at_utc:null,
  finished_at_utc:null,first_simulation_week:null,snapshot_id:null,publication_committed:false,error_code:null,error_message:null,
 };
 const newer={...original,refresh_id:"newer",created_at_utc:"2026-11-20T12:05:00Z"};
 const finished={...newer,status,stage:status,publication_committed:status==="completed",
  snapshot_id:status==="completed"?snapshot.snapshot_id:null,error_message:status==="failed"?"New refresh failed.":null};
 const previous={...original,status:"failed",stage:"failed",error_message:"Original refresh failed."} as const;
 const discover=vi.spyOn(api,"currentRefresh").mockResolvedValue(original);
 const submit=vi.spyOn(api,"submitRefresh");
 const cancel=vi.spyOn(api,"cancelRefresh");
 const poll=vi.spyOn(api,"refresh").mockImplementation(async id=>id===original.refresh_id?previous:finished);
 vi.spyOn(api,"refreshResult").mockResolvedValue({api_schema_version:"1.0",refresh:finished,snapshot,
  unresolved_capabilities:[],validation_findings:[]});
 const onPublished=vi.fn();
 const props={automaticStart:true,onClose:()=>{},onPublished};
 const {rerender}=render(<LeagueRefresh {...props} open/>);
 await screen.findByText("Original refresh failed.");
 rerender(<LeagueRefresh {...props} open={false}/>);
 discover.mockResolvedValue(newer);
 rerender(<LeagueRefresh {...props} open/>);
 await waitFor(()=>expect(poll).toHaveBeenCalledWith(newer.refresh_id,expect.any(AbortSignal)));
 if(status==="completed"){
  await waitFor(()=>expect(onPublished).toHaveBeenCalledWith(expect.objectContaining({refresh:finished})));
 }else{
  expect(await screen.findByText("New refresh failed.")).toBeInTheDocument();
  expect(onPublished).not.toHaveBeenCalled();
 }
 expect(screen.queryByText("Original refresh failed.")).not.toBeInTheDocument();
 expect(submit).not.toHaveBeenCalled();
 expect(cancel).not.toHaveBeenCalled();
});
