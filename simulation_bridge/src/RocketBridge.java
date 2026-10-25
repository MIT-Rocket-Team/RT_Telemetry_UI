import java.io.*;
import java.nio.file.*;
import java.util.*;
import jakarta.json.*;
import com.google.inject.Guice;
import com.google.inject.AbstractModule;
import com.google.inject.util.Modules;
import java.util.prefs.*;
import info.openrocket.core.startup.Application;
import info.openrocket.core.plugin.PluginModule;
import info.openrocket.swing.startup.GuiModule;
import info.openrocket.core.file.GeneralRocketLoader;
import info.openrocket.core.file.motor.GeneralMotorLoader;
import info.openrocket.core.motor.ThrustCurveMotor;
import info.openrocket.core.motor.MotorConfiguration;
import info.openrocket.core.logging.Warning;
import info.openrocket.core.document.OpenRocketDocument;
import info.openrocket.core.document.Simulation;
import info.openrocket.core.simulation.*;
import info.openrocket.core.simulation.listeners.NewControlStepListener;
import info.openrocket.core.models.wind.*;
import info.openrocket.core.database.MotorDatabaseLoader;
import info.openrocket.core.database.motor.MotorDatabase;
import info.openrocket.core.database.motor.ThrustCurveMotorSetDatabase;
import info.openrocket.core.preferences.ApplicationPreferences;
import info.openrocket.swing.gui.util.SwingPreferences;

/** One fresh JVM per nominal simulation; no GUI and no imported script execution. */
public class RocketBridge {
    public static class MemoryPreferencesFactory implements PreferencesFactory {
        private final Preferences root=new MemoryPreferences(null,"");
        public Preferences userRoot(){return root;}
        public Preferences systemRoot(){return root;}
    }
    static class MemoryPreferences extends AbstractPreferences {
        private final Map<String,String> values=new HashMap<>();
        MemoryPreferences(AbstractPreferences parent,String name){super(parent,name);}
        protected void putSpi(String key,String value){values.put(key,value);}
        protected String getSpi(String key){return values.get(key);}
        protected void removeSpi(String key){values.remove(key);}
        protected void removeNodeSpi(){values.clear();}
        protected String[] keysSpi(){return values.keySet().toArray(String[]::new);}
        protected String[] childrenNamesSpi(){return new String[0];}
        protected AbstractPreferences childSpi(String name){return new MemoryPreferences(this,name);}
        protected void syncSpi(){}
        protected void flushSpi(){}
    }
    public static class JobPreferences extends SwingPreferences {
        @Override public List<File> getUserThrustCurveFiles(){return List.of();}
    }
    static double number(JsonObject object, String key) { return object.getJsonNumber(key).doubleValue(); }
    public static void main(String[] args) {
        Path output=null;
        try {
            if (args.length == 1 && args[0].equals("--probe")) {
                System.out.println("RocketBridge v1 / Java " + System.getProperty("java.version"));
                return;
            }
            if (args.length != 1) throw new IllegalArgumentException("Expected request.json");
            JsonObject request;
            try (InputStream in=Files.newInputStream(Path.of(args[0])); JsonReader reader=Json.createReader(in)) {
                request=reader.readObject();
            }
            if(request.getInt("schema_version")!=1 || !request.getBoolean("nominal"))
                throw new IllegalArgumentException("Only v1 nominal simulation is supported");
            output=Path.of(request.getString("output"));
            Files.createDirectories(output);
            Locale.setDefault(Locale.US);
            GuiModule module=new GuiModule();
            MotorDatabaseLoader motors=new MotorDatabaseLoader();
            Application.setInjector(Guice.createInjector(Modules.override(module).with(new AbstractModule(){
                @Override protected void configure(){
                    bind(ApplicationPreferences.class).to(JobPreferences.class).asEagerSingleton();
                    bind(MotorDatabase.class).toProvider(motors::getDatabase);
                    bind(ThrustCurveMotorSetDatabase.class).toProvider(motors::getDatabase);
                }
            }),new PluginModule()));
            module.startLoader();
            motors.startLoading();
            motors.getDatabase(); // Block without Swing's modal loading dialog.
            GeneralMotorLoader motorLoader=new GeneralMotorLoader();
            for(JsonValue value:request.getJsonArray("motor_files")) {
                Path path=Path.of(((JsonString)value).getString());
                try(InputStream in=Files.newInputStream(path)) {
                    for(ThrustCurveMotor.Builder builder:motorLoader.load(in,path.getFileName().toString()))
                        motors.getDatabase().addMotor(builder.build());
                }
            }
            // This fork reads the inertia override in MassCalculation, even without a controller listener.
            NewControlStepListener.FLAG_OVERRIDE_JXX=false;
            NewControlStepListener.useRK6=false;
            NewControlStepListener.WIND_EVENT_1_GUST=0;
            NewControlStepListener.WIND_EVENT_2_GUST=0;
            NewControlStepListener.WIND_EVENT_3_GUST=0;
            GeneralRocketLoader loader=new GeneralRocketLoader(new File(request.getString("model")));
            OpenRocketDocument document=loader.load();
            List<String> missingMotors=new ArrayList<>();
            for(Warning warning:loader.getWarnings()) {
                if(warning instanceof Warning.MissingMotor missing)
                    missingMotors.add((missing.getManufacturer()==null ? "" : missing.getManufacturer()+" ")
                        +missing.getDesignation());
            }
            if(!missingMotors.isEmpty())
                throw new IOException("Missing motor thrust curve: "+String.join(", ",missingMotors)
                    +". In Mission & wind, use Select custom motor files to add the matching .eng or .rse file.");
            int index=request.getInt("simulation_index",0);
            if(index<0 || index>=document.getSimulationCount())
                throw new IllegalArgumentException("Model needs a saved simulation with a motor; index is out of range");
            Simulation saved=document.getSimulation(index);
            Collection<MotorConfiguration> activeMotors=document.getRocket()
                .getFlightConfiguration(saved.getFlightConfigurationId()).getActiveMotors();
            if(activeMotors.isEmpty())
                throw new IOException("The selected simulation '"+saved.getName()
                    +"' has no active motor. Assign a motor to an active stage in OpenRocket and save the model.");
            JsonArrayBuilder resolvedMotors=Json.createArrayBuilder();
            for(MotorConfiguration motor:activeMotors) {
                JsonObjectBuilder entry=Json.createObjectBuilder()
                    .add("designation",motor.getMotor().getDesignation())
                    .add("digest",motor.getMotor().getDigest());
                if(motor.getMotor() instanceof ThrustCurveMotor curve)
                    entry.add("manufacturer",curve.getManufacturer().getDisplayName());
                resolvedMotors.add(entry);
            }
            Simulation simulation=new Simulation(document.getRocket());
            simulation.setFlightConfigurationId(saved.getFlightConfigurationId());
            simulation.getSimulationExtensions().clear();
            SimulationOptions options=simulation.getOptions();
            options.setLaunchLatitude(number(request,"latitude"));
            options.setLaunchLongitude(number(request,"longitude"));
            options.setLaunchAltitude(number(request,"altitude"));
            options.setLaunchRodLength(number(request,"rail_length"));
            options.setLaunchIntoWind(false);
            options.setLaunchRodAngle(Math.toRadians(number(request,"rail_tilt")));
            options.setLaunchRodDirection(Math.toRadians(number(request,"rail_heading")));
            options.setRandomSeed(request.getInt("seed"));
            options.setTimeStep(.05);
            options.setMaxSimulationTime(600);
            options.setISAAtmosphere(true);
            options.setWindModelType(WindModelType.MULTI_LEVEL);
            MultiLevelPinkNoiseWindModel wind=options.getMultiLevelWindModel();
            wind.clearLevels();
            wind.setAltitudeReference(WindModel.AltitudeReference.AGL);
            for(JsonValue value:request.getJsonArray("wind")) {
                JsonObject layer=value.asJsonObject();
                double east=number(layer,"east"),north=number(layer,"north");
                // OpenRocket velocity uses sin(direction), cos(direction): direction is TOWARD.
                double direction=(Math.atan2(east,north)+Math.PI*2)%(Math.PI*2);
                wind.addWindLevel(number(layer,"height"),Math.hypot(east,north),direction,0.0);
            }
            simulation.simulate();
            FlightData data=simulation.getSimulatedData();
            if(data==null || data.getBranchCount()==0) throw new IOException("No simulation branch");
            FlightDataBranch branch=data.getBranch(0);
            List<Double> time=branch.get(FlightDataType.TYPE_TIME);
            List<Double> east=branch.get(FlightDataType.TYPE_POSITION_X);
            List<Double> north=branch.get(FlightDataType.TYPE_POSITION_Y);
            List<Double> up=branch.get(FlightDataType.TYPE_ALTITUDE);
            FlightDataType[] motionTypes={FlightDataType.TYPE_ORIENTATION_QW,FlightDataType.TYPE_ORIENTATION_QX,
                FlightDataType.TYPE_ORIENTATION_QY,FlightDataType.TYPE_ORIENTATION_QZ,
                FlightDataType.TYPE_VELOCITY_X,FlightDataType.TYPE_VELOCITY_Y,FlightDataType.TYPE_VELOCITY_Z,
                FlightDataType.TYPE_ROLL_RATE,FlightDataType.TYPE_PITCH_RATE,FlightDataType.TYPE_YAW_RATE};
            List<List<Double>> motion=new ArrayList<>();
            for(FlightDataType type:motionTypes) motion.add(branch.get(type));
            int written=0;
            try(PrintWriter csv=new PrintWriter(Files.newBufferedWriter(output.resolve("trajectory.csv.tmp")))) {
                csv.println("time_s,east_m,north_m,up_m,qw,qx,qy,qz,velocity_east_m_s,velocity_north_m_s,velocity_up_m_s,roll_rate_rad_s,pitch_rate_rad_s,yaw_rate_rad_s");
                double previous=-Double.MAX_VALUE;
                for(int i=0;i<time.size();i++) {
                    double t=time.get(i),e=east.get(i),n=north.get(i),u=up.get(i);
                    if(!Double.isFinite(t+e+n+u) || t<=previous) continue;
                    csv.printf(Locale.US,"%.8f,%.8f,%.8f,%.8f",t,e,n,u);
                    for(List<Double> column:motion) {
                        Double value=column==null || i>=column.size() ? null : column.get(i);
                        csv.print(",");
                        if(value!=null && Double.isFinite(value)) csv.printf(Locale.US,"%.10g",value);
                    }
                    csv.println();
                    previous=t;written++;
                }
            }
            if(written<2) throw new IOException("Simulation ended before producing a flight trajectory. "
                +"Check ignition and active stages. OpenRocket warnings: "+data.getWarningSet()
                +"; events: "+branch.getEvents());
            JsonArrayBuilder origin=Json.createArrayBuilder().add(number(request,"latitude"))
                .add(number(request,"longitude")).add(number(request,"origin_ellipsoid_altitude"));
            JsonArrayBuilder flightEvents=Json.createArrayBuilder();
            for(FlightEvent event:branch.getEvents()) {
                if(!Double.isFinite(event.getTime())) continue;
                flightEvents.add(Json.createObjectBuilder().add("time",event.getTime())
                    .add("type",event.getType().name())
                    .add("source",event.getSource()==null ? "" : event.getSource().getName())
                    .add("source_id",event.getSource()==null ? "" : event.getSource().getID().toString()));
            }
            JsonObject result=Json.createObjectBuilder().add("schema_version",1).add("frame","ENU")
                .add("units","m,s").add("altitude_datum","launch_relative").add("origin",origin)
                .add("name","OpenRocket nominal · "+document.getRocket().getName())
                .add("motors",resolvedMotors).add("saved_simulation",saved.getName())
                .add("flight_events",flightEvents).add("visuals_schema_version",1)
                .add("attitude_frame","body_to_ENU").add("body_axis","+Z")
                .add("branch",0).add("branch_count",data.getBranchCount()).add("synthetic",false)
                .add("controlled_model_validated",false).add("inertia_override",false)
                .add("solver","ModifiedEventSimulationEngine / useRK6=false")
                .add("extensions","disabled").add("turbulence_standard_deviation",0)
                .add("warnings",loader.getWarnings().toString()+"; "+data.getWarningSet().toString())
                .add("rows",written).build();
            try(JsonWriter writer=Json.createWriter(Files.newOutputStream(output.resolve("trajectory.json.tmp")))) {
                writer.writeObject(result);
            }
            Files.move(output.resolve("trajectory.csv.tmp"),output.resolve("trajectory.csv"),StandardCopyOption.REPLACE_EXISTING);
            Files.move(output.resolve("trajectory.json.tmp"),output.resolve("trajectory.json"),StandardCopyOption.REPLACE_EXISTING);
            System.out.println("Completed "+written+" nominal trajectory rows");
            System.exit(0); // Shut down engine background resource-loader threads.
        } catch(Throwable error) {
            error.printStackTrace(System.err);
            if(output!=null) {
                try(JsonWriter writer=Json.createWriter(Files.newOutputStream(output.resolve("error.json")))) {
                    writer.writeObject(Json.createObjectBuilder().add("schema_version",1)
                        .add("message",error.getMessage()==null ? error.toString() : error.getMessage()).build());
                } catch(Exception reportError) {
                    reportError.printStackTrace(System.err);
                }
            }
            System.exit(1);
        }
    }
}
