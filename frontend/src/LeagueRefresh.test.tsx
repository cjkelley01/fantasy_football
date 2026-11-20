import {render,waitFor} from "@testing-library/react";
import {LeagueRefresh} from "./LeagueRefresh";
import {api} from "./lib/api";

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
