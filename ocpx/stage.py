from casadi import MX, substitute, Function, vcat, depends_on, vertcat, jacobian, veccat
from .freetime import FreeTime


class Stage:
    def __init__(self, ocp, t0=0, T=1):
        self.ocp = ocp
        self.states = []
        self.controls = []
        self.parameters = []
        self._param_grid = dict()
        self._param_vals = dict()
        self._state_der = dict()
        self._constraints = []
        self._objective = 0
        self._initial = dict()
        self._T = T
        self._t0 = t0
        self._placeholders = dict()
        self._placeholder_callbacks = dict()
        self._create_variables(t0, T)

    def _create_placeholder_expr(self, expr, callback_name):
        r = MX.sym("r_" + callback_name, MX(expr).sparsity())
        self._placeholders[r] = expr
        self._placeholder_callbacks[r] = callback_name
        return r

    def bake_placeholders(self, method):
        placeholders = dict()
        ks = list(self._placeholders.keys())
        vs = [self._placeholders[k] for k in ks]

        #if depends_on(vcat(expr), vcat(subst_from)):

        #vs = substitute(vs, subst_from, subs_to)

        for k, v in zip(ks, vs):
            callback = getattr(method, 'fill_placeholders_' + self._placeholder_callbacks[k])
            placeholders[k] = callback(self, v)

        return placeholders

    def _create_variables(self, t0, T):
        if self.is_free_time():
            self.T = self._create_placeholder_expr(0, 'T')
        else:
            self.T = T

        if self.is_free_starttime():
            self.t0 = self._create_placeholder_expr(0, 't0')
        else:
            self.t0 = t0

        self.tf = self.t0 + self.T

        self.t = MX.sym('t')

    def is_free_time(self):
        return isinstance(self._T, FreeTime)

    def is_free_starttime(self):
        return isinstance(self._t0, FreeTime)

    def get_jacobian(self, der, state):
        return jacobian(der, state)

    def state(self, n_rows=1, n_cols=1):
        """
        Create a state
        """
        # Create a placeholder symbol with a dummy name (see #25)
        x = MX.sym("x", n_rows, n_cols)
        self.states.append(x)
        self.ocp.is_transcribed = False
        return x

    def parameter(self, n_rows=1, n_cols=1, grid = ''):
        """
        Create a parameter
        """
        # Create a placeholder symbol with a dummy name (see #25)
        p = MX.sym("p", n_rows, n_cols)
        self._param_grid[p] = grid
        self.parameters.append(p)
        self.ocp.is_transcribed = False
        return p

    def control(self, n_rows=1, n_cols=1, order=0):
        if order >= 1:
            u = self.state(n_rows, n_cols)
            helper_u = self.control(n_rows=n_rows, n_cols=n_cols, order=order - 1)
            self.set_der(u, helper_u)
            return u

        u = MX.sym("u", n_rows, n_cols)
        self.controls.append(u)
        self.ocp.is_transcribed = False
        return u

    def set_value(self, parameter, value):
        if self.ocp.is_transcribed:
            self._method.set_value(self, self.ocp._method.opti, parameter, value)            
        else:
            self._param_vals[parameter] = value

    def set_der(self, state, der):
        self.ocp.is_transcribed = False
        self._state_der[state] = der

    def integral(self, expr, grid='inf'):
        if grid=='inf':
            I = self.state()
            self.set_der(I, expr)
            self.subject_to(self.at_t0(I) == 0)
            return self.at_tf(I)
        else:
            return self._create_placeholder_expr(expr, 'integral_control')

    def subject_to(self, constr):
        self.ocp.is_transcribed = False
        self._constraints.append(constr)

    def set_initial(self, var, expr):
        self._initial[var] = expr

    def at_t0(self, expr):
        return self._create_placeholder_expr(expr, 'at_t0')

    def at_tf(self, expr):
        return self._create_placeholder_expr(expr, 'at_tf')

    def add_objective(self, term):
        self.ocp.is_transcribed = False
        self._objective = self._objective + term

    def method(self, method):
        self._method = method

    @property
    def x(self):
        return veccat(*self.states)

    @property
    def u(self):
        return veccat(*self.controls)

    @property
    def p(self):
        return veccat(*self.parameters)

    @property
    def nx(self):
        return self.x.numel()

    @property
    def nu(self):
        return self.u.numel()

    @property
    def np(self):
        return self.p.numel()

    def is_trajectory(self, expr):
        return depends_on(expr, vertcat(self.x, self.u))

    # Internal methods
    def _ode(self):
        ode = veccat(*[self._state_der[k] for k in self.states])
        return Function('ode', [self.x, self.u, self.p], [ode], ["x", "u", "p"], ["ode"])

    def _boundary_constraints_expr(self):
        return [c for c in self._constraints if not self.is_trajectory(c)]

    def _path_constraints_expr(self):
        return [c for c in self._constraints if self.is_trajectory(c)]

    def _expr_apply(self, expr, **kwargs):
        """
        Substitute placeholder symbols with actual decision variables,
        or expressions involving decision variables
        """
        subst_from, subst_to = self.get_subst_set(**kwargs)
        return substitute([expr], subst_from, subst_to)[0]

    def get_subst_set(self, **kwargs):
        subst_from = []
        subst_to = []
        if "t" in kwargs:
            subst_from.append(self.t)
            subst_to.append(kwargs["t"])
        if "x" in kwargs:
            subst_from.append(self.x)
            subst_to.append(kwargs["x"])
        if "u" in kwargs:
            subst_from.append(self.u)
            subst_to.append(kwargs["u"])
        if self.is_free_starttime() and "t0" in kwargs:
            subst_from.append(self.t0)
            subst_to.append(kwargs["t0"])

        if self.is_free_time() and "T" in kwargs:
            subst_from.append(self.T)
            subst_to.append(kwargs["T"])

        if "p" in kwargs:
            subst_from.append(self.p)
            subst_to.append(kwargs["p"])

        return (subst_from, subst_to)

    def subst_expr(self, expr):
        for k in range(self._method.N):
            subst_from, subst_to = self.get_subst_set(
                x=self._method.X[k],
                u=self._method.U[k],
                T=self._method.T,
                p=self._method.P,
                t0=self._method.t0,
            )
            expr = substitute([expr], subst_from, subst_to)[0]

        return expr

    _constr_apply = _expr_apply

    def _expr_to_function(self, expr):
        return Function('helper', [self.x, self.u], [expr], ["x", "u"], ["out"])
